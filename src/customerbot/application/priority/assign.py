"""Matrix-driven priority assignment on ticket creation (flow §7a)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from customerbot.application.priority.actions import (
    REASON_MANUAL_OVERRIDE,
    PriorityChangePayload,
    set_priority_action_id,
)
from customerbot.application.priority.matrix import PriorityMatrix
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import (
    RENEWAL_3MO_DAYS,
    RENEWAL_6MO_DAYS,
    Org,
    Ticket,
    customer_weight,
)
from customerbot.domain.tickets.ports import EventLogRepositoryPort
from customerbot.domain.tickets.value_objects import (
    CustomerWeight,
    Priority,
    Severity,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_OVERRIDABLE_TIERS = (Priority.P1, Priority.P2, Priority.P3, Priority.P4)
"""Override buttons the SE sees on the rationale DM. P0 is excluded by spec
§5a — it's only assignable via the manual candidate-flag flow."""


class AssignPriority:
    """Two-step:

    - `suggest(org, severity)` returns the matrix-driven priority. Pure.
    - `record_and_offer_override(ticket, se_user_id)` writes the prio-change
      event row (`null → Pn`, reason `"matrix lookup"`) and DMs SE the
      rationale with `[P1][P2][P3][P4]` override buttons.
    """

    def __init__(
        self,
        matrix: PriorityMatrix,
        events: EventLogRepositoryPort,
        slack: SlackPort,
    ) -> None:
        self._matrix = matrix
        self._events = events
        self._slack = slack

    def _weight_for(self, org: Org | None, today: date) -> CustomerWeight:
        if org is None:
            return CustomerWeight.LOW
        return customer_weight(
            org.acv_tier,
            org.sentiment,
            org.renewal_status,
            renewal_date=org.renewal_date,
            today=today,
        )

    def suggest(self, org: Org | None, severity: Severity) -> Priority:
        weight = self._weight_for(org, _utcnow().date())
        return self._matrix.lookup(weight, severity)

    async def record_and_offer_override(
        self,
        ticket: Ticket,
        org: Org | None,
        *,
        se_user_id: str,
    ) -> None:
        if ticket.id is None:
            return
        today = _utcnow().date()
        weight = self._weight_for(org, today)
        # 1. Event log: null → matrix-assigned priority.
        await self._events.append_prio_change(
            ticket_id=ticket.id,
            from_priority=None,
            to_priority=ticket.priority,
            by_user_id=None,
            at=_utcnow(),
            reason="matrix lookup",
        )
        # 2. DM SE the rationale + override buttons.
        await self._slack.send_dm_blocks(
            se_user_id,
            _rationale_blocks(ticket, org, weight, today),
            text=(f"{ticket.display_id} priority set to {ticket.priority.value} (matrix lookup)"),
        )


def _renewal_descriptor(org: Org, today: date) -> str:
    """Human-readable renewal signal for the rationale DM. Prefers the date
    (the live weighting input) and notes which proximity bump is active."""
    if org.renewal_date is not None:
        days = (org.renewal_date - today).days
        if days <= RENEWAL_3MO_DAYS:
            tag = "≤3mo ⇒ ×1.5"
        elif days <= RENEWAL_6MO_DAYS:
            tag = "≤6mo ⇒ ×1.25"
        else:
            tag = "far off"
        return f"renewal {org.renewal_date.isoformat()} ({tag})"
    if org.renewal_status is not None:
        return f"renewal: {org.renewal_status.value}"
    return "renewal: unknown"


def _rationale_blocks(
    ticket: Ticket, org: Org | None, weight: CustomerWeight, today: date
) -> list[dict[str, Any]]:
    if org is not None:
        org_line = (
            f"Customer weight: *{weight.value}* "
            f"(ACV: {(org.acv_tier.value if org.acv_tier else 'unknown')}, "
            f"sentiment: {(org.sentiment.value if org.sentiment else 'unknown')}, "
            f"{_renewal_descriptor(org, today)})"
        )
    else:
        org_line = f"Customer weight: *{weight.value}* (org metadata unset; defaulted)"

    rationale = (
        f"*{ticket.display_id}* set to *{ticket.priority.value}*.\n"
        f"{org_line}\n"
        f"Severity: *{ticket.severity.value}*."
    )
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": tier.value},
            "action_id": set_priority_action_id(tier),
            "value": PriorityChangePayload(
                ticket_id=ticket.id or 0,
                priority=tier,
                reason=REASON_MANUAL_OVERRIDE,
            ).encode(),
            "style": "primary" if tier == ticket.priority else None,
        }
        for tier in _OVERRIDABLE_TIERS
    ]
    # Slack rejects `style: None` — strip those keys.
    for b in buttons:
        if b.get("style") is None:
            b.pop("style", None)

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": rationale}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": ":point_right: Override with one of the buttons below.",
                }
            ],
        },
        {"type": "actions", "elements": buttons},
    ]
