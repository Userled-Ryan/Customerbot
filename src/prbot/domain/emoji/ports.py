from typing import Protocol

from prbot.domain.emoji.value_objects import EmojiConfig


class EmojiConfigResolverPort(Protocol):
    """Port for resolving emoji config based on scope keys (most-specific first).

    Implementations walk the scope_keys list and return the first matching
    EmojiConfig, falling back to the global default.
    """

    async def resolve(self, scope_keys: list[str]) -> EmojiConfig: ...
