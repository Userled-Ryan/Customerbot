from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import (
    ChannelOrgCacheRow,
    DraftFormSessionRow,
    PendingDedupeChoiceRow,
    PendingPrioOverrideRow,
    PendingReclassifySendRow,
    PrioMatrixReviewStateRow,
    SLADMStateRow,
    WeeklyDigestStateRow,
)
from customerbot.domain.bot_state.entities import (
    ChannelOrgEntry,
    DraftFormSession,
    ModalKind,
    PendingDedupeChoice,
    PendingPrioOverride,
    PendingReclassifySend,
    PrioMatrixReviewState,
    SLADMRecord,
    SLAStage,
    SLAState,
    WeeklyDigestState,
)

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _str_to_dt(s: str) -> datetime:
    return datetime.strptime(s, _DT_FMT)


def _opt_dt_to_str(dt: datetime | None) -> str | None:
    return _dt_to_str(dt) if dt else None


def _opt_str_to_dt(s: str | None) -> datetime | None:
    return _str_to_dt(s) if s else None


# --- DraftFormSession ---


def _row_to_draft(row: DraftFormSessionRow) -> DraftFormSession:
    return DraftFormSession(
        id=row.id,
        slack_view_id=row.slack_view_id,
        modal_kind=ModalKind(row.modal_kind),
        invoker_user_id=row.invoker_user_id,
        invoker_channel_id=row.invoker_channel_id,
        invoker_thread_ts=row.invoker_thread_ts,
        payload_json=row.payload_json,
        created_at=_str_to_dt(row.created_at),
        expires_at=_str_to_dt(row.expires_at),
    )


class SQLiteDraftFormSessionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, draft: DraftFormSession) -> DraftFormSession:
        async with self._session_factory() as session:
            row = DraftFormSessionRow(
                slack_view_id=draft.slack_view_id,
                modal_kind=draft.modal_kind.value,
                invoker_user_id=draft.invoker_user_id,
                invoker_channel_id=draft.invoker_channel_id,
                invoker_thread_ts=draft.invoker_thread_ts,
                payload_json=draft.payload_json,
                created_at=_dt_to_str(draft.created_at),
                expires_at=_dt_to_str(draft.expires_at),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_draft(row)

    async def get_by_view_id(self, slack_view_id: str) -> DraftFormSession | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DraftFormSessionRow).where(
                    DraftFormSessionRow.slack_view_id == slack_view_id
                )
            )
            row = result.scalar_one_or_none()
            return _row_to_draft(row) if row else None

    async def delete(self, session_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(DraftFormSessionRow).where(DraftFormSessionRow.id == session_id)
            )
            await session.commit()

    async def delete_expired(self, *, now: datetime) -> int:
        cutoff = _dt_to_str(now)
        async with self._session_factory() as session:
            result = await session.execute(
                delete(DraftFormSessionRow).where(DraftFormSessionRow.expires_at < cutoff)
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[union-attr]


# --- ChannelOrgCache ---


def _row_to_channel_org(row: ChannelOrgCacheRow) -> ChannelOrgEntry:
    return ChannelOrgEntry(
        slack_channel_id=row.slack_channel_id,
        org_id=row.org_id,
        last_synced_at=_str_to_dt(row.last_synced_at),
    )


class SQLiteChannelOrgCacheRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(self, entry: ChannelOrgEntry) -> None:
        async with self._session_factory() as session:
            stmt = (
                insert(ChannelOrgCacheRow)
                .values(
                    slack_channel_id=entry.slack_channel_id,
                    org_id=entry.org_id,
                    last_synced_at=_dt_to_str(entry.last_synced_at),
                )
                .on_conflict_do_update(
                    index_elements=["slack_channel_id"],
                    set_={
                        "org_id": entry.org_id,
                        "last_synced_at": _dt_to_str(entry.last_synced_at),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def get(self, slack_channel_id: str) -> ChannelOrgEntry | None:
        async with self._session_factory() as session:
            row = await session.get(ChannelOrgCacheRow, slack_channel_id)
            return _row_to_channel_org(row) if row else None


# --- SLA DM state ---


def _row_to_sla(row: SLADMStateRow) -> SLADMRecord:
    return SLADMRecord(
        ticket_id=row.ticket_id,
        stage=SLAStage(row.stage),
        last_state=SLAState(row.last_state),
        last_dm_at=_opt_str_to_dt(row.last_dm_at),
        updated_at=_str_to_dt(row.updated_at),
    )


class SQLiteSLADMStateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, ticket_id: int, stage: SLAStage) -> SLADMRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SLADMStateRow).where(
                    SLADMStateRow.ticket_id == ticket_id,
                    SLADMStateRow.stage == stage.value,
                )
            )
            row = result.scalar_one_or_none()
            return _row_to_sla(row) if row else None

    async def upsert(
        self,
        ticket_id: int,
        stage: SLAStage,
        state: SLAState,
        last_dm_at: datetime | None,
        *,
        now: datetime,
    ) -> None:
        last_dm_str = _opt_dt_to_str(last_dm_at)
        async with self._session_factory() as session:
            stmt = (
                insert(SLADMStateRow)
                .values(
                    ticket_id=ticket_id,
                    stage=stage.value,
                    last_state=state.value,
                    last_dm_at=last_dm_str,
                    updated_at=_dt_to_str(now),
                )
                .on_conflict_do_update(
                    index_elements=["ticket_id", "stage"],
                    set_={
                        "last_state": state.value,
                        "last_dm_at": last_dm_str,
                        "updated_at": _dt_to_str(now),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()


# --- PendingDedupeChoice ---


def _row_to_dedupe(row: PendingDedupeChoiceRow) -> PendingDedupeChoice:
    return PendingDedupeChoice(
        id=row.id,
        candidate_ticket_id=row.candidate_ticket_id,
        payload_json=row.payload_json,
        invoker_user_id=row.invoker_user_id,
        dm_channel_id=row.dm_channel_id,
        dm_message_ts=row.dm_message_ts,
        created_at=_str_to_dt(row.created_at),
        expires_at=_str_to_dt(row.expires_at),
    )


class SQLitePendingDedupeChoiceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, choice: PendingDedupeChoice) -> PendingDedupeChoice:
        async with self._session_factory() as session:
            row = PendingDedupeChoiceRow(
                candidate_ticket_id=choice.candidate_ticket_id,
                payload_json=choice.payload_json,
                invoker_user_id=choice.invoker_user_id,
                dm_channel_id=choice.dm_channel_id,
                dm_message_ts=choice.dm_message_ts,
                created_at=_dt_to_str(choice.created_at),
                expires_at=_dt_to_str(choice.expires_at),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_dedupe(row)

    async def get(self, choice_id: int) -> PendingDedupeChoice | None:
        async with self._session_factory() as session:
            row = await session.get(PendingDedupeChoiceRow, choice_id)
            return _row_to_dedupe(row) if row else None

    async def update_dm_metadata(
        self, choice_id: int, dm_channel_id: str, dm_message_ts: str
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(PendingDedupeChoiceRow)
                .where(PendingDedupeChoiceRow.id == choice_id)
                .values(dm_channel_id=dm_channel_id, dm_message_ts=dm_message_ts)
            )
            await session.commit()

    async def delete(self, choice_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(PendingDedupeChoiceRow).where(PendingDedupeChoiceRow.id == choice_id)
            )
            await session.commit()

    async def delete_expired(self, *, now: datetime) -> int:
        cutoff = _dt_to_str(now)
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PendingDedupeChoiceRow).where(PendingDedupeChoiceRow.expires_at < cutoff)
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[union-attr]


# --- PendingPrioOverride ---


def _row_to_prio(row: PendingPrioOverrideRow) -> PendingPrioOverride:
    return PendingPrioOverride(
        id=row.id,
        ticket_id=row.ticket_id,
        suggested_priority=row.suggested_priority,
        dm_channel_id=row.dm_channel_id,
        dm_message_ts=row.dm_message_ts,
        created_at=_str_to_dt(row.created_at),
        expires_at=_str_to_dt(row.expires_at),
    )


class SQLitePendingPrioOverrideRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, override: PendingPrioOverride) -> PendingPrioOverride:
        async with self._session_factory() as session:
            row = PendingPrioOverrideRow(
                ticket_id=override.ticket_id,
                suggested_priority=override.suggested_priority,
                dm_channel_id=override.dm_channel_id,
                dm_message_ts=override.dm_message_ts,
                created_at=_dt_to_str(override.created_at),
                expires_at=_dt_to_str(override.expires_at),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_prio(row)

    async def get(self, override_id: int) -> PendingPrioOverride | None:
        async with self._session_factory() as session:
            row = await session.get(PendingPrioOverrideRow, override_id)
            return _row_to_prio(row) if row else None

    async def delete(self, override_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(PendingPrioOverrideRow).where(PendingPrioOverrideRow.id == override_id)
            )
            await session.commit()

    async def delete_expired(self, *, now: datetime) -> int:
        cutoff = _dt_to_str(now)
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PendingPrioOverrideRow).where(PendingPrioOverrideRow.expires_at < cutoff)
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[union-attr]


# --- PendingReclassifySend ---


def _row_to_reclass(row: PendingReclassifySendRow) -> PendingReclassifySend:
    return PendingReclassifySend(
        id=row.id,
        ticket_id=row.ticket_id,
        reclassification_event_id=row.reclassification_event_id,
        recipients_json=row.recipients_json,
        draft_text=row.draft_text,
        dm_channel_id=row.dm_channel_id,
        dm_message_ts=row.dm_message_ts,
        created_at=_str_to_dt(row.created_at),
        expires_at=_str_to_dt(row.expires_at),
    )


class SQLitePendingReclassifySendRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, send: PendingReclassifySend) -> PendingReclassifySend:
        async with self._session_factory() as session:
            row = PendingReclassifySendRow(
                ticket_id=send.ticket_id,
                reclassification_event_id=send.reclassification_event_id,
                recipients_json=send.recipients_json,
                draft_text=send.draft_text,
                dm_channel_id=send.dm_channel_id,
                dm_message_ts=send.dm_message_ts,
                created_at=_dt_to_str(send.created_at),
                expires_at=_dt_to_str(send.expires_at),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_reclass(row)

    async def get(self, send_id: int) -> PendingReclassifySend | None:
        async with self._session_factory() as session:
            row = await session.get(PendingReclassifySendRow, send_id)
            return _row_to_reclass(row) if row else None

    async def update_dm_metadata(
        self, send_id: int, dm_channel_id: str, dm_message_ts: str
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(PendingReclassifySendRow)
                .where(PendingReclassifySendRow.id == send_id)
                .values(dm_channel_id=dm_channel_id, dm_message_ts=dm_message_ts)
            )
            await session.commit()

    async def delete(self, send_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(PendingReclassifySendRow).where(PendingReclassifySendRow.id == send_id)
            )
            await session.commit()

    async def delete_expired(self, *, now: datetime) -> int:
        cutoff = _dt_to_str(now)
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PendingReclassifySendRow).where(PendingReclassifySendRow.expires_at < cutoff)
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[union-attr]


# --- PrioMatrixReviewState (singleton row) ---


def _row_to_review_state(row: PrioMatrixReviewStateRow) -> PrioMatrixReviewState:
    return PrioMatrixReviewState(
        last_ack_at=_opt_str_to_dt(row.last_ack_at),
        last_snooze_until=_opt_str_to_dt(row.last_snooze_until),
        updated_at=_str_to_dt(row.updated_at),
    )


class SQLitePrioMatrixReviewStateRepository:
    """Singleton-row table. `get()` lazily inserts a default row on first read."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self) -> PrioMatrixReviewState:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PrioMatrixReviewStateRow).order_by(PrioMatrixReviewStateRow.id).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                now_str = _dt_to_str(datetime.now(UTC).replace(tzinfo=None))
                row = PrioMatrixReviewStateRow(updated_at=now_str)
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return _row_to_review_state(row)

    async def update(self, state: PrioMatrixReviewState, *, now: datetime) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PrioMatrixReviewStateRow).order_by(PrioMatrixReviewStateRow.id).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = PrioMatrixReviewStateRow()
                session.add(row)
                await session.flush()
            row.last_ack_at = _opt_dt_to_str(state.last_ack_at)
            row.last_snooze_until = _opt_dt_to_str(state.last_snooze_until)
            row.updated_at = _dt_to_str(now)
            await session.commit()


# --- WeeklyDigestState (singleton row) ---


def _row_to_digest_state(row: WeeklyDigestStateRow) -> WeeklyDigestState:
    return WeeklyDigestState(
        last_fired_at=_opt_str_to_dt(row.last_fired_at),
        updated_at=_str_to_dt(row.updated_at),
    )


class SQLiteWeeklyDigestStateRepository:
    """Singleton-row table. `get()` lazily inserts a default row on first read."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self) -> WeeklyDigestState:
        async with self._session_factory() as session:
            result = await session.execute(
                select(WeeklyDigestStateRow).order_by(WeeklyDigestStateRow.id).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                now_str = _dt_to_str(datetime.now(UTC).replace(tzinfo=None))
                row = WeeklyDigestStateRow(updated_at=now_str)
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return _row_to_digest_state(row)

    async def update(self, state: WeeklyDigestState, *, now: datetime) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(WeeklyDigestStateRow).order_by(WeeklyDigestStateRow.id).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = WeeklyDigestStateRow()
                session.add(row)
                await session.flush()
            row.last_fired_at = _opt_dt_to_str(state.last_fired_at)
            row.updated_at = _dt_to_str(now)
            await session.commit()
