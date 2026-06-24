"""Reply-needed toggle (ticket-card button).

The SE flags a live ticket as "waiting on a reply" straight from its card; a
second click clears it. The flag drives the card badge and the daily 5pm
reply-needed digest (`reply_digest.ReplyNeededDigestJob`). It's plain metadata
— no event-log row — so this use case just flips the bit and re-renders the
card.

Clearing the flag is the SE's honest "I've replied / no longer waiting" signal,
which is why we don't try to auto-detect replies from the thread.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ToggleReplyNeeded:
    """Handle the `Reply needed` / `Clear reply-needed` card button."""

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
            logger.warning("Reply-needed toggled on missing ticket %s", ticket_id)
            return None
        new_value = not ticket.reply_needed
        _ = by_user_id  # carried for future audit
        await self._tickets.set_reply_needed(ticket.id, new_value, now=_utcnow())
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
        logger.info(
            "Reply-needed %s for %s",
            "set" if new_value else "cleared",
            ticket.display_id,
        )
        return await self._tickets.get(ticket.id)
