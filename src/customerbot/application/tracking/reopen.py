"""Reopen-ticket lifecycle handler (ambiguity #7, plan Chunk 9).

Reopen acts on a *retired* ticket — one that's `Resolved` or `Closed`, i.e.
exactly the states whose card collapses to a struck line + Reopen button.
Both go back to `In progress` so the strikethrough clears and the full card
returns.

A `Closed` (dropped) ticket carries a `closed_at`, so the 30-day window
applies: older than 30 days → bot DMs SE suggesting they create a new linked
ticket (relation `supersedes`) instead of resurrecting stale state. A
`Resolved` ticket has no `closed_at`, so it always reopens (no window).

The 30-day window is measured from `closed_at`. We use the column rather
than the event log because (a) it's denormalised onto the ticket on close,
(b) a ticket can be reopened and re-closed multiple times — `closed_at`
tracks the latest, which is the right reference for "how long has this
been closed".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from customerbot.application.intake.ticket_card import _RETIRED_STATUSES, refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import TicketStatus

logger = logging.getLogger(__name__)

REOPEN_WINDOW = timedelta(days=30)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class ReopenResult:
    ticket: Ticket | None
    reopened: bool
    suggested_new_ticket: bool


class ReopenTicket:
    """Handle the `Reopen` button click."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._se_user_id = se_user_id
        self._linear = linear

    async def execute(
        self, *, ticket_id: int, by_user_id: str, sync_to_linear: bool = True
    ) -> ReopenResult:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Reopen clicked on missing ticket %s", ticket_id)
            return ReopenResult(ticket=None, reopened=False, suggested_new_ticket=False)

        # Reopen only makes sense on a retired (Resolved / Closed) ticket — the
        # states that actually render a Reopen button. A live ticket is a no-op.
        if ticket.status not in _RETIRED_STATUSES:
            logger.info(
                "Reopen clicked on %s with status %s — no-op",
                ticket.display_id,
                ticket.status.value,
            )
            return ReopenResult(ticket=ticket, reopened=False, suggested_new_ticket=False)

        now = _utcnow()
        prior_status = ticket.status
        closed_at = ticket.closed_at
        if closed_at is None or (now - closed_at) <= REOPEN_WINDOW:
            await self._tickets.update_status(ticket.id, TicketStatus.IN_PROGRESS, now=now)
            # `closed_at` (hence the 30-day window) only exists for dropped/closed
            # tickets; resolved ones have none and always reopen.
            note = (
                "reopened-within-30d"
                if prior_status == TicketStatus.CLOSED
                else "reopened (from resolved)"
            )
            await self._events.append_status_change(
                ticket_id=ticket.id,
                from_status=prior_status,
                to_status=TicketStatus.IN_PROGRESS,
                by_user_id=by_user_id,
                at=now,
                note=note,
            )
            await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
            # Reflect the reopen onto the Linear mirror so it doesn't stay
            # Done/Canceled. Skipped when driven by an inbound Linear event.
            if sync_to_linear and self._linear is not None:
                await self._linear.sync_state(ticket.id)
            refreshed = await self._tickets.get(ticket.id)
            return ReopenResult(
                ticket=refreshed,
                reopened=True,
                suggested_new_ticket=False,
            )

        # Stale — suggest creating a new linked ticket.
        age_days = (now - closed_at).days
        await self._slack.send_dm_blocks(
            self._se_user_id,
            stale_reopen_blocks(ticket, closed_age_days=age_days),
            text=f"Reopen suggestion: {ticket.display_id}",
        )
        return ReopenResult(ticket=ticket, reopened=False, suggested_new_ticket=True)


def stale_reopen_blocks(ticket: Ticket, *, closed_age_days: int) -> list[dict[str, Any]]:
    closed_iso = ticket.closed_at.isoformat(timespec="seconds") if ticket.closed_at else "unknown"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":no_entry: *{ticket.display_id}* was closed "
                    f"*{closed_age_days} days ago* — that's outside the 30-day "
                    f"reopen window.\n"
                    f"Create a new ticket and link it to {ticket.display_id} "
                    f"instead, so the older context stays preserved."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_Original: {ticket.title} — closed at {closed_iso}._",
                }
            ],
        },
    ]
