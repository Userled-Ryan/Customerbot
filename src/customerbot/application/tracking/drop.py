"""Drop-ticket lifecycle handler (the `Drop` ticket-card button).

`Drop` is the explicit "we're done with this — stop reminding me" action.
It transitions any live ticket straight to `Closed`, which removes it from
the live set so the SLA scan, confirmation nudges, and CSM pre-close nudges
all stop firing immediately (they only act on live / awaiting tickets).

This is the manual counterpart to the 7-day auto-close in `auto_close.py`:
same terminal status, same `closed_at` stamp, so the existing `Reopen`
button (Closed → In progress within 30 days) works on a dropped ticket with
no extra wiring.

The bot never messages customers here — dropping is a purely internal state
change. The card is re-rendered so it visibly retires (struck-through title,
lock prefix, Reopen-only button set).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import notify_csms_status_change, refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import TicketStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class DropResult:
    ticket: Ticket | None
    dropped: bool


class DropTicket:
    """Handle the `Drop` button click — close a live ticket immediately."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._linear = linear

    async def execute(
        self, *, ticket_id: int, by_user_id: str, sync_to_linear: bool = True
    ) -> DropResult:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Drop clicked on missing ticket %s", ticket_id)
            return DropResult(ticket=None, dropped=False)

        if ticket.status == TicketStatus.CLOSED:
            logger.info("Ticket %s already closed — drop no-op", ticket.display_id)
            return DropResult(ticket=ticket, dropped=False)

        now = _utcnow()
        prior_status = ticket.status
        await self._tickets.update_status(ticket.id, TicketStatus.CLOSED, now=now)
        await self._events.append_status_change(
            ticket_id=ticket.id,
            from_status=prior_status,
            to_status=TicketStatus.CLOSED,
            by_user_id=by_user_id,
            at=now,
            note="dropped",
        )
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

        # CSM alert — only for SE-initiated drops. A Linear-driven cancel
        # (`sync_to_linear=False`) is already announced by the inbound handler.
        if sync_to_linear:
            await notify_csms_status_change(
                self._slack,
                self._tickets,
                self._orgs,
                ticket,
                status_label="Dropped",
                by_user_id=by_user_id,
            )

        # Linear mirror: a drop maps to Canceled (distinct from Done) so the CTO
        # dashboard separates dropped tickets from resolved ones.
        if sync_to_linear and self._linear is not None:
            await self._linear.mark_done_silently(ticket.id, state=LinearWorkflowState.CANCELED)

        refreshed = await self._tickets.get(ticket.id)
        return DropResult(ticket=refreshed, dropped=True)
