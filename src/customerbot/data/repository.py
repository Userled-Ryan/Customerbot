from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import (
    ChannelCursorRow,
    TrackedConversationRow,
    TrackedKeywordRow,
    UserSettingsRow,
)
from customerbot.domain.tracking.entities import TrackedConversation, UserSettings
from customerbot.domain.tracking.value_objects import ConversationStatus

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _str_to_dt(s: str) -> datetime:
    return datetime.strptime(s, _DT_FMT)


def _row_to_entity(row: TrackedConversationRow) -> TrackedConversation:
    return TrackedConversation(
        id=row.id,
        ticket_number=row.ticket_number,
        channel_id=row.channel_id,
        thread_ts=row.thread_ts,
        channel_name=row.channel_name,
        category=row.category,
        status=ConversationStatus(row.status),
        context=row.context,
        last_ryan_reply_at=_str_to_dt(row.last_ryan_reply_at) if row.last_ryan_reply_at else None,
        opened_at=_str_to_dt(row.opened_at),
        reminder_sent_at=_str_to_dt(row.reminder_sent_at) if row.reminder_sent_at else None,
        reminder_interval_hours=row.reminder_interval_hours,
    )


class SQLiteConversationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(self, conversation: TrackedConversation) -> None:
        async with self._session_factory() as session:
            count_result = await session.execute(
                select(func.count())
                .select_from(TrackedConversationRow)
                .where(TrackedConversationRow.status == ConversationStatus.OPEN.value)
            )
            next_number = (count_result.scalar() or 0) + 1
            stmt = (
                insert(TrackedConversationRow)
                .values(
                    ticket_number=next_number,
                    channel_id=conversation.channel_id,
                    thread_ts=conversation.thread_ts,
                    channel_name=conversation.channel_name,
                    category=conversation.category,
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

    async def find_by_thread(self, channel_id: str, thread_ts: str) -> TrackedConversation | None:
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
                TrackedConversationRow.ticket_number == ticket_id,
                TrackedConversationRow.status == ConversationStatus.OPEN.value,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _row_to_entity(row) if row else None

    async def repack_ticket_numbers(self) -> None:
        async with self._session_factory() as session:
            stmt = (
                select(TrackedConversationRow)
                .where(TrackedConversationRow.status == ConversationStatus.OPEN.value)
                .order_by(TrackedConversationRow.ticket_number)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            for i, row in enumerate(rows, start=1):
                row.ticket_number = i
            await session.commit()

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

    async def update_last_reply(self, channel_id: str, thread_ts: str, at: datetime) -> None:
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

    async def update_reminder_sent(self, channel_id: str, thread_ts: str, at: datetime) -> None:
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

    async def update_reminder_interval(self, ticket_id: int, hours: int | None) -> None:
        async with self._session_factory() as session:
            stmt = (
                update(TrackedConversationRow)
                .where(TrackedConversationRow.ticket_number == ticket_id)
                .values(reminder_interval_hours=hours)
            )
            await session.execute(stmt)
            await session.commit()


class SQLiteUserSettingsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, user_id: str) -> UserSettings | None:
        async with self._session_factory() as session:
            stmt = select(UserSettingsRow).where(UserSettingsRow.user_id == user_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return UserSettings(
                user_id=row.user_id,
                timezone=row.timezone,
                default_reminder_hours=row.default_reminder_hours,
                daily_digest_enabled=bool(row.daily_digest_enabled),
                last_morning_digest_date=row.last_morning_digest_date,
                last_evening_digest_date=row.last_evening_digest_date,
            )

    async def save(self, settings: UserSettings) -> None:
        async with self._session_factory() as session:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = (
                sqlite_insert(UserSettingsRow)
                .values(
                    user_id=settings.user_id,
                    timezone=settings.timezone,
                    default_reminder_hours=settings.default_reminder_hours,
                    daily_digest_enabled=1 if settings.daily_digest_enabled else 0,
                    last_morning_digest_date=settings.last_morning_digest_date,
                    last_evening_digest_date=settings.last_evening_digest_date,
                )
                .on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={
                        "timezone": settings.timezone,
                        "default_reminder_hours": settings.default_reminder_hours,
                        "daily_digest_enabled": 1 if settings.daily_digest_enabled else 0,
                        "last_morning_digest_date": settings.last_morning_digest_date,
                        "last_evening_digest_date": settings.last_evening_digest_date,
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()


class SQLiteKeywordRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add(self, word: str, category: str | None = None) -> bool:
        normalized = word.strip().lower()
        if not normalized:
            return False
        normalized_category = category.strip().lower() if category else None
        async with self._session_factory() as session:
            stmt = (
                insert(TrackedKeywordRow)
                .values(word=normalized, category=normalized_category)
                .on_conflict_do_update(
                    index_elements=["word"],
                    set_={"category": normalized_category},
                )
            )
            await session.execute(stmt)
            await session.commit()
            return True

    async def remove(self, word: str) -> bool:
        normalized = word.strip().lower()
        if not normalized:
            return False
        async with self._session_factory() as session:
            stmt = delete(TrackedKeywordRow).where(TrackedKeywordRow.word == normalized)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def list_all(self) -> list[tuple[str, str | None]]:
        async with self._session_factory() as session:
            stmt = select(TrackedKeywordRow.word, TrackedKeywordRow.category).order_by(
                TrackedKeywordRow.word
            )
            result = await session.execute(stmt)
            return [(row.word, row.category) for row in result.all()]


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
