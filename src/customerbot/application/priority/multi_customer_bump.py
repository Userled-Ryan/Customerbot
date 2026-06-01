"""Multi-customer prio-bump suggestion (flow §5c, min-spec §7b).

Triggered when a ticket gains an additional `Affected org` — i.e., from a
dedupe merge (Chunk 6) or the `Add affected org` button (Chunk 9). The bot
*suggests* a bump; SE confirms via the same priority-change button as the
initial override flow.

Thresholds (flow §5c):
- 2 orgs → +1 tier
- 3+ orgs → at least P1
- 5+ orgs on a critical-path feature → P0 candidate

Per §16 of the flow doc, the bot never auto-applies these bumps.
"""

from __future__ import annotations

import logging
from typing import Any

from customerbot.application.priority.actions import (
    ACTION_DISMISS_PRIO_DM,
    ACTION_SET_PRIORITY,
    REASON_MULTI_CUSTOMER_BUMP,
    PriorityChangePayload,
)
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import Priority, bump_one_tier

logger = logging.getLogger(__name__)


class MultiCustomerBumpCheck:
    def __init__(
        self,
        tickets: TicketRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        critical_path_features: list[str],
    ) -> None:
        self._tickets = tickets
        self._slack = slack
        self._se_user_id = se_user_id
        self._critical_path_features = {f.lower() for f in critical_path_features}

    async def execute(self, ticket_id: int) -> Priority | None:
        """Decide if a bump should be suggested and DM SE. Returns the suggested
        priority if a DM was sent, else None.

        Pure suggestion — does NOT change the ticket. The DM contains a button
        that SE clicks to apply, routing through `ApplyPriorityChange`.
        """
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            return None
        orgs = await self._tickets.list_orgs(ticket.id)
        n_orgs = len(orgs)
        if n_orgs < 2:
            return None
        suggested = self._suggest(n_orgs, ticket)
        if suggested is None or suggested == ticket.priority:
            return None
        await self._slack.send_dm_blocks(
            self._se_user_id,
            _bump_blocks(ticket, n_orgs, suggested),
            text=f"Multi-customer bump suggested for {ticket.display_id}",
        )
        return suggested

    def _suggest(self, n_orgs: int, ticket: Ticket) -> Priority | None:
        critical = (
            ticket.feature is not None and ticket.feature.lower() in self._critical_path_features
        )
        if n_orgs >= 5 and critical:
            return Priority.P0  # P0 candidate suggestion — still requires SE click
        if n_orgs >= 3:
            # min(current, P1) — bump to P1 if currently below.
            order = (Priority.P4, Priority.P3, Priority.P2, Priority.P1, Priority.P0)
            current_rank = order.index(ticket.priority)
            p1_rank = order.index(Priority.P1)
            return Priority.P1 if current_rank < p1_rank else None
        if n_orgs == 2:
            bumped = bump_one_tier(ticket.priority)
            return bumped if bumped != ticket.priority else None
        return None


def _bump_blocks(ticket: Ticket, n_orgs: int, suggested: Priority) -> list[dict[str, Any]]:
    if suggested == Priority.P0:
        # P0 path uses a different reason code so the audit trail shows
        # "P0 candidate" rather than just "multi-customer bump".
        from customerbot.application.priority.actions import REASON_P0_CANDIDATE

        reason = REASON_P0_CANDIDATE
        headline = (
            f":rotating_light: *{ticket.display_id}* now affects {n_orgs} customers "
            f"on critical-path feature *{ticket.feature}* — *P0 candidate.*\n"
            f"Current: {ticket.priority.value}. Confirm P0?"
        )
    else:
        reason = REASON_MULTI_CUSTOMER_BUMP
        headline = (
            f"*{ticket.display_id}* now affects {n_orgs} customers — "
            f"suggest bump *{ticket.priority.value} → {suggested.value}*. Confirm?"
        )

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": (
                            f"Set {suggested.value}" if suggested != Priority.P0 else "Set P0"
                        ),
                    },
                    "action_id": ACTION_SET_PRIORITY,
                    "value": PriorityChangePayload(
                        ticket_id=ticket.id or 0,
                        priority=suggested,
                        reason=reason,
                    ).encode(),
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Skip"},
                    "action_id": ACTION_DISMISS_PRIO_DM,
                    "value": "skip",
                },
            ],
        },
    ]
