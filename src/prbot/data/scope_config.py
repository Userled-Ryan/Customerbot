from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prbot.config import EmojiConfig
from prbot.data.database import ScopeConfigRow

logger = logging.getLogger(__name__)


class ScopeConfigEmojiResolver:
    """Resolves emoji config by walking scope keys from most-specific to least-specific.

    Falls back to the global default EmojiConfig if no scope matches.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        default: EmojiConfig,
    ) -> None:
        self._session_factory = session_factory
        self._default = default

    async def resolve(self, scope_keys: list[str]) -> EmojiConfig:
        if not scope_keys:
            return self._default

        async with self._session_factory() as session:
            stmt = select(ScopeConfigRow).where(ScopeConfigRow.scope_key.in_(scope_keys))
            result = await session.execute(stmt)
            rows = {row.scope_key: row for row in result.scalars().all()}

        # Walk from most-specific to least-specific
        for key in scope_keys:
            if key in rows:
                logger.debug("Resolved emoji config from scope %s", key)
                return EmojiConfig.model_validate(rows[key].emoji_config)

        return self._default
