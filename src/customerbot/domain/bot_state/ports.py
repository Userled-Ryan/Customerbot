from __future__ import annotations

from datetime import datetime
from typing import Protocol

from customerbot.domain.bot_state.entities import (
    ChannelOrgEntry,
    DraftFormSession,
    PendingDedupeChoice,
    PendingPrioOverride,
    PendingReclassifySend,
    PrioMatrixReviewState,
    SLADMRecord,
    SLAStage,
    SLAState,
)


class DraftFormSessionRepositoryPort(Protocol):
    async def create(self, draft: DraftFormSession) -> DraftFormSession: ...

    async def get_by_view_id(self, slack_view_id: str) -> DraftFormSession | None: ...

    async def delete(self, session_id: int) -> None: ...

    async def delete_expired(self, *, now: datetime) -> int: ...


class ChannelOrgCacheRepositoryPort(Protocol):
    async def upsert(self, entry: ChannelOrgEntry) -> None: ...

    async def get(self, slack_channel_id: str) -> ChannelOrgEntry | None: ...


class SLADMStateRepositoryPort(Protocol):
    async def get(self, ticket_id: int, stage: SLAStage) -> SLADMRecord | None: ...

    async def upsert(
        self,
        ticket_id: int,
        stage: SLAStage,
        state: SLAState,
        last_dm_at: datetime | None,
        *,
        now: datetime,
    ) -> None: ...


class PendingDedupeChoiceRepositoryPort(Protocol):
    async def create(self, choice: PendingDedupeChoice) -> PendingDedupeChoice: ...

    async def get(self, choice_id: int) -> PendingDedupeChoice | None: ...

    async def update_dm_metadata(
        self, choice_id: int, dm_channel_id: str, dm_message_ts: str
    ) -> None: ...

    async def delete(self, choice_id: int) -> None: ...

    async def delete_expired(self, *, now: datetime) -> int: ...


class PendingPrioOverrideRepositoryPort(Protocol):
    async def create(self, override: PendingPrioOverride) -> PendingPrioOverride: ...

    async def get(self, override_id: int) -> PendingPrioOverride | None: ...

    async def delete(self, override_id: int) -> None: ...

    async def delete_expired(self, *, now: datetime) -> int: ...


class PendingReclassifySendRepositoryPort(Protocol):
    async def create(self, send: PendingReclassifySend) -> PendingReclassifySend: ...

    async def get(self, send_id: int) -> PendingReclassifySend | None: ...

    async def update_dm_metadata(
        self, send_id: int, dm_channel_id: str, dm_message_ts: str
    ) -> None: ...

    async def delete(self, send_id: int) -> None: ...

    async def delete_expired(self, *, now: datetime) -> int: ...


class PrioMatrixReviewStateRepositoryPort(Protocol):
    async def get(self) -> PrioMatrixReviewState: ...

    async def update(self, state: PrioMatrixReviewState, *, now: datetime) -> None: ...
