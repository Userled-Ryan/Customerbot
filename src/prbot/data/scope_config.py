from __future__ import annotations

import logging

from prbot.domain.ports import ScopeSettingsPort
from prbot.domain.value_objects import EmojiConfig

logger = logging.getLogger(__name__)

EMOJI_SETTING_KEY = "emoji"


class ScopeConfigEmojiResolver:
    """Resolves emoji config by walking scope keys from most-specific to least-specific.

    Falls back to the global default EmojiConfig if no scope has an override.
    """

    def __init__(
        self,
        settings: ScopeSettingsPort,
        default: EmojiConfig,
    ) -> None:
        self._settings = settings
        self._default = default

    async def resolve(self, scope_keys: list[str]) -> EmojiConfig:
        raw = await self._settings.get(scope_keys, EMOJI_SETTING_KEY)
        if raw is None:
            return self._default
        return EmojiConfig.model_validate(raw)
