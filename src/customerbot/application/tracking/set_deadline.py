"""Set-deadline button + modal flow.

Two-step:

1. SE clicks `Set deadline` (or `Change deadline`) on a ticket card →
   `OpenSetDeadlineModal` opens the datepicker pre-filled with the
   current deadline.
2. SE picks (or clears) the date → `SubmitDeadline` updates
   `ticket.deadline` and refreshes the card.

Deadline changes don't write to the event log — `deadline` is metadata,
not a state transition. If reporting needs an audit trail later we can
add an `event_deadline_changes` table; for v1 SE's the only writer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)


# `view_builder(ticket_id, current_deadline) -> view JSON`.
ViewBuilder = Callable[..., dict[str, Any]]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OpenSetDeadlineModal:
    """Open the datepicker modal in response to the `Set deadline` click."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        view_builder: ViewBuilder,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._view_builder = view_builder

    async def execute(self, *, trigger_id: str, ticket_id: int) -> str | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None:
            logger.warning("Set deadline clicked on missing ticket %s", ticket_id)
            return None
        view = self._view_builder(ticket_id=ticket_id, current_deadline=ticket.deadline)
        return await self._slack.open_view(trigger_id, view)


class SubmitDeadline:
    """Handle the `set_deadline` modal `view_submission`."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._orgs = orgs

    async def execute(
        self, *, ticket_id: int, deadline: date | None, by_user_id: str
    ) -> Ticket | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Set deadline submitted for missing ticket %s", ticket_id)
            return None
        if ticket.deadline == deadline:
            logger.info(
                "Set deadline no-op: %s already %s",
                ticket.display_id,
                deadline.isoformat() if deadline else "unset",
            )
            return ticket
        _ = by_user_id  # carried for future audit (event_deadline_changes)
        await self._tickets.update_deadline(ticket.id, deadline, now=_utcnow())
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
        return await self._tickets.get(ticket.id)
