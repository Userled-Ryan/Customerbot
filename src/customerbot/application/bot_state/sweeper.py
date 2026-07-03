from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from customerbot.domain.bot_state.ports import (
    DraftFormSessionRepositoryPort,
    PendingDedupeChoiceRepositoryPort,
    PendingPrioOverrideRepositoryPort,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SweepEphemeralState:
    """Background sweeper for the ephemeral bot-state tables.

    - Drops unsubmitted `draft_form_sessions` past their 30-min expiry (§3a).
    - Drops the `pending_*` tables past their 7-day expiry (housekeeping).

    The "pending" rows have a 7-day window because they represent SE-confirmation
    DMs; if SE never clicks, those drafts are stale and not worth keeping.
    """

    def __init__(
        self,
        drafts: DraftFormSessionRepositoryPort,
        pending_dedupe: PendingDedupeChoiceRepositoryPort,
        pending_prio: PendingPrioOverrideRepositoryPort,
    ) -> None:
        self._drafts = drafts
        self._pending_dedupe = pending_dedupe
        self._pending_prio = pending_prio

    async def execute(self, *, now: datetime | None = None) -> int:
        when = now or _utcnow()
        deleted = 0
        deleted += await self._drafts.delete_expired(now=when)
        deleted += await self._pending_dedupe.delete_expired(now=when)
        deleted += await self._pending_prio.delete_expired(now=when)
        return deleted

    async def run_loop(self, interval_seconds: int = 60) -> None:
        while True:
            try:
                deleted = await self.execute()
                if deleted:
                    logger.info("Swept %d expired bot-state rows", deleted)
            except Exception:
                logger.exception("Error in ephemeral-state sweeper loop")
            await asyncio.sleep(interval_seconds)
