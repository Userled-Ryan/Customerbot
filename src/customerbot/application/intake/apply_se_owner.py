"""Apply an SE-owner change from the ticket card's SE-owner dropdown.

Wired to `ACTION_SET_SE_OWNER`. Mirrors `ApplyPriorityChange`: reassign the
owner in SQL, then reflect it everywhere — redraw the card and push the owner
onto the Linear mirror as the issue assignee.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from customerbot.application.intake.se_owner_actions import SeOwnerChangePayload
from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ApplySeOwnerChange:
    def __init__(
        self,
        tickets: TicketRepositoryPort,
        slack: SlackPort,
        orgs: OrgRepositoryPort,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._slack = slack
        self._orgs = orgs
        self._linear = linear

    async def execute(self, payload: SeOwnerChangePayload, *, by_user_id: str) -> str | None:
        existing = await self._tickets.get(payload.ticket_id)
        if existing is None or existing.id is None:
            logger.warning("SE-owner change clicked on missing ticket %s", payload.ticket_id)
            return None
        if existing.se_owner_user_id == payload.owner_user_id:
            # No-op click (SE picked the already-current owner).
            return existing.se_owner_user_id
        now = _utcnow()
        await self._tickets.update_se_owner(existing.id, payload.owner_user_id, now=now)
        logger.info(
            "SE owner of %s changed to %s by %s",
            existing.display_id,
            payload.owner_user_id,
            by_user_id,
        )
        # Reflect the change everywhere: the in-channel card and the Linear mirror.
        # `sync_assignee` is a no-op in Linear terms while a dev holds the issue
        # (the dev owner wins) — the SE owner still changes on the Slack side.
        await refresh_card(self._slack, self._tickets, self._orgs, existing.id)
        if self._linear is not None:
            await self._linear.sync_assignee(existing.id)
        return payload.owner_user_id
