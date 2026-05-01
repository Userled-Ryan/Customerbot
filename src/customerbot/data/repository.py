from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import ChannelCursorRow, TrackedConversationRow
from customerbot.domain.tracking.entities import TrackedConversation
from customerbot.domain.tracking.value_objects import ConversationCategory, ConversationStatus

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _str_to_dt(s: str) -> datetime:
    return datetime.strptime(s, _DT_FMT)


def _row_to_entity(row: TrackedConversationRow) -> TrackedConversation:
    return TrackedConversation(
        id=row.id,
        channel_id=row.channel_id,
        thread_ts=row.thread_ts,
        channel_name=row.channel_name,
        category=ConversationCategory(row.category),
        status=ConversationStatus(row.status),
        context=row.context,
        last_ryan_reply_at=_str_to_dt(row.last_ryan_reply_at) if row.last_ryan_reply_at else None,
        opened_at=_str_to_dt(row.opened_at),
        reminder_sent_at=_str_to_dt(row.reminder_sent_at) if row.reminder_sent_at else None,
    )


class SQLiteConversationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(self, conversation: TrackedConversation) -> None:
        async with self._session_factory() as session:
            stmt = (
                insert(TrackedConversationRow)
                .values(
                    channel_id=conversation.channel_id,
                    thread_ts=conversation.thread_ts,
                    channel_name=conversation.channel_name,
                    category=conversation.category.value,
                    status=conversation.status.value,
                    context=conversation.context,
                    last_ryan_reply_at=_dt_to_str(conversation.last_ryan_reply_at)
                    if conversation.last_ryan_reply_at
                    else None,
                    opened_at=_dt_to_str(conversation.opened_at),
                    reminder_sent_at=_dt_to_str(conversation.reminder_sent_at)
                    if conversation.reminder_sent_at
                    else None,
                )
                .on_conflict_do_nothing(index_elements=["channel_id", "thread_ts"])
            )
            await session.execute(stmt)
            await session.commit()

    async def find_by_thread(
        self, channel_id: str, thread_ts: str
    ) -> TrackedConversation | None:
        async with self._session_factory() as session:
            stmt = select(TrackedConversationRow).where(
                TrackedConversationRow.channel_id == channel_id,
                TrackedConversationRow.thread_ts == thread_ts,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _row_to_entity(row) if row else None

    async def find_by_id(self, ticket_id: int) -> TrackedConversation | None:
        async with self._session_factory() as session:
            stmt = select(TrackedConversationRow).where(
                TrackedConversationRow.id == ticket_id
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _row_to_entity(row) if row else None

    async def find_open(self) -> list[TrackedConversation]:
        async with self._session_factory() as session:
            stmt = select(TrackedConversationRow).where(
                TrackedConversationRow.status == ConversationStatus.OPEN.value
            )
            result = await session.execute(stmt)
            return [_row_to_entity(row) for row in result.scalars().all()]

    async def find_overdue(self, hours: int) -> list[TrackedConversation]:
        open_convos = await self.find_open()
        return [c for c in open_convos if c.is_overdue(hours)]

    async def update_last_reply(
        self, channel_id: str, thread_ts: str, at: datetime
    ) -> None:
        async with self._session_factory() as session:
            stmt = (
                update(TrackedConversationRow)
                .where(
                    TrackedConversationRow.channel_id == channel_id,
                    TrackedConversationRow.thread_ts == thread_ts,
                )
                .values(last_ryan_reply_at=_dt_to_str(at))
            )
            await session.execute(stmt)
            await session.commit()

    async def update_status(
        self, channel_id: str, thread_ts: str, status: ConversationStatus
    ) -> None:
        async with self._session_factory() as session:
            stmt = (
                update(TrackedConversationRow)
                .where(
                    TrackedConversationRow.channel_id == channel_id,
                    TrackedConversationRow.thread_ts == thread_ts,
                )
                .values(status=status.value)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_reminder_sent(
        self, channel_id: str, thread_ts: str, at: datetime
    ) -> None:
        async with self._session_factory() as session:
            stmt = (
                update(TrackedConversationRow)
                .where(
                    TrackedConversationRow.channel_id == channel_id,
                    TrackedConversationRow.thread_ts == thread_ts,
                )
                .values(reminder_sent_at=_dt_to_str(at))
            )
            await session.execute(stmt)
            await session.commit()


class SQLiteChannelCursorRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_cursor(self, integration_id: str, channel_id: str) -> str | None:
        async with self._session_factory() as session:
            stmt = select(ChannelCursorRow.last_seen_ts).where(
                ChannelCursorRow.integration_id == integration_id,
                ChannelCursorRow.channel_id == channel_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def upsert_cursor(self, integration_id: str, channel_id: str, ts: str) -> None:
        async with self._session_factory() as session:
            stmt = (
                insert(ChannelCursorRow)
                .values(
                    integration_id=integration_id,
                    channel_id=channel_id,
                    last_seen_ts=ts,
                )
                .on_conflict_do_update(
                    index_elements=["integration_id", "channel_id"],
                    set_={"last_seen_ts": ts},
                )
            )
            await session.execute(stmt)
            await session.commit()
