"""Application-layer operations for the per-scope self-review mute flag.

When the flag is set at a scope, prbot skips the ``commented`` emoji reaction
if the PR author is the same person who just commented on their own PR.
"""

from prbot.domain.common.ports import ScopeSettingsPort

MUTE_SELF_REVIEWS_KEY = "mute_self_reviews"


class ManageSelfReviews:
    """Use cases for the per-scope self-review mute flag."""

    def __init__(self, settings: ScopeSettingsPort) -> None:
        self._settings = settings

    async def mute(self, scope_key: str) -> bool:
        """Set the mute flag at a scope. Returns True if newly set."""
        current = await self._settings.get([scope_key], MUTE_SELF_REVIEWS_KEY)
        if current:
            return False
        await self._settings.set(scope_key, MUTE_SELF_REVIEWS_KEY, True)
        return True

    async def unmute(self, scope_key: str) -> bool:
        """Clear the mute flag at a scope. Returns True if it was set."""
        return await self._settings.unset(scope_key, MUTE_SELF_REVIEWS_KEY)

    async def muted_at(self, scope_keys: list[str]) -> str | None:
        """Return the most-specific scope where mute is set, or None.

        Matches the hierarchy-walk semantic used at the webhook-handler
        call site: the most-specific scope wins.
        """
        grouped = await self._settings.get_all_at(scope_keys, MUTE_SELF_REVIEWS_KEY)
        for scope_key in scope_keys:
            if grouped.get(scope_key):
                return scope_key
        return None
