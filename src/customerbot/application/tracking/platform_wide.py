"""Platform-wide toggle (ticket-card button).

Bug tickets carry a `platform-wide` vs `customer-specific` subtype. The SE
flips it straight from the card — a bug born customer-specific gets marked
platform-wide once it's confirmed broad (and back again). It's the same
subtype the reclassify modal edits, surfaced as a one-click toggle so the
common case doesn't need the full modal.

Only meaningful for Bug tickets; the card only renders the button for them,
and this use case no-ops on any other type so a stale click can't corrupt a
Config/FAQ/Feature-request subtype.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import TicketSubtype, TicketType

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TogglePlatformWide:
    """Handle the `Mark platform-wide` / `Mark customer-specific` card button."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._orgs = orgs

    async def execute(self, *, ticket_id: int, by_user_id: str) -> Ticket | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Platform-wide toggled on missing ticket %s", ticket_id)
            return None
        if ticket.type != TicketType.BUG:
            logger.info(
                "Platform-wide toggle ignored for non-bug %s (%s)",
                ticket.display_id,
                ticket.type.value,
            )
            return ticket
        _ = by_user_id  # carried for future audit
        new_subtype = (
            TicketSubtype.CUSTOMER_SPECIFIC
            if ticket.subtype == TicketSubtype.PLATFORM_WIDE
            else TicketSubtype.PLATFORM_WIDE
        )
        await self._tickets.update_type_subtype(ticket.id, ticket.type, new_subtype, now=_utcnow())
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
        logger.info("Marked %s %s", ticket.display_id, new_subtype.value)
        return await self._tickets.get(ticket.id)
