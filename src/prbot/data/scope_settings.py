from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prbot.data.database import ScopeSettingRow

logger = logging.getLogger(__name__)


class SQLiteScopeSettingsRepository:
    """Generic key/value-per-scope storage backed by the ``scope_settings`` table.

    Values are JSON-serializable. Individual features layer typed accessors
    on top of this (emoji config, user exclusions, …).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, scope_keys: list[str], key: str) -> object | None:
        if not scope_keys:
            return None

        async with self._session_factory() as session:
            stmt = select(ScopeSettingRow).where(
                ScopeSettingRow.scope_key.in_(scope_keys),
                ScopeSettingRow.key == key,
            )
            result = await session.execute(stmt)
            rows = {row.scope_key: row.value for row in result.scalars().all()}

        for scope_key in scope_keys:
            if scope_key in rows:
                return rows[scope_key]
        return None

    async def get_all_at(self, scope_keys: list[str], key: str) -> dict[str, object]:
        if not scope_keys:
            return {}

        async with self._session_factory() as session:
            stmt = select(ScopeSettingRow).where(
                ScopeSettingRow.scope_key.in_(scope_keys),
                ScopeSettingRow.key == key,
            )
            result = await session.execute(stmt)
            return {row.scope_key: row.value for row in result.scalars().all()}

    async def set(self, scope_key: str, key: str, value: object) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ScopeSettingRow, (scope_key, key))
            if existing is None:
                session.add(ScopeSettingRow(scope_key=scope_key, key=key, value=value))
            else:
                existing.value = value
            await session.commit()
            logger.debug("Set setting %r for scope %s", key, scope_key)

    async def unset(self, scope_key: str, key: str) -> bool:
        async with self._session_factory() as session:
            existing = await session.get(ScopeSettingRow, (scope_key, key))
            if existing is None:
                return False
            await session.delete(existing)
            await session.commit()
            logger.debug("Unset setting %r for scope %s", key, scope_key)
            return True
