from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import ChannelCursorRow


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
