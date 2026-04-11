from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prbot.data.database import UserExclusionRow

logger = logging.getLogger(__name__)


class SQLiteUserExclusionRepository:
    """Stores per-scope GitHub username exclusions in a relational table.

    Scope resolution walks from most-specific to least-specific — a
    username excluded at *any* matching scope is considered excluded.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_excluded(self, scope_keys: list[str], github_username: str) -> bool:
        if not scope_keys:
            return False

        lower = github_username.lower()
        async with self._session_factory() as session:
            stmt = select(UserExclusionRow).where(
                UserExclusionRow.scope_key.in_(scope_keys),
            )
            result = await session.execute(stmt)
            for row in result.scalars():
                if row.username.lower() == lower:
                    return True
        return False

    async def add(self, scope_key: str, github_username: str) -> bool:
        async with self._session_factory() as session:
            existing = await session.get(UserExclusionRow, (scope_key, github_username))
            if existing is not None:
                return False
            session.add(UserExclusionRow(scope_key=scope_key, username=github_username))
            await session.commit()
            logger.info("Excluded user %r in scope %s", github_username, scope_key)
            return True

    async def remove(self, scope_key: str, github_username: str) -> bool:
        async with self._session_factory() as session:
            existing = await session.get(UserExclusionRow, (scope_key, github_username))
            if existing is None:
                return False
            await session.delete(existing)
            await session.commit()
            logger.info("Re-included user %r in scope %s", github_username, scope_key)
            return True

    async def list_excluded(self, scope_key: str) -> list[str]:
        async with self._session_factory() as session:
            stmt = select(UserExclusionRow.username).where(
                UserExclusionRow.scope_key == scope_key,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
