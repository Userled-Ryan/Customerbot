"""Application-layer operations for managing scope-level configuration.

Each public method is a self-contained use case (command or query) that
operates on the domain model.  Integration layers (Slack slash commands,
Discord bot commands, future REST API, …) call these and render the
result in their own format.
"""

import logging
from dataclasses import dataclass

from prbot.domain.ports import ScopeSettingsPort, UserExclusionPort

MUTE_SELF_REVIEWS_KEY = "mute_self_reviews"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExclusionResult:
    """Outcome of an exclude/include operation."""

    username: str
    excluded: bool  # True = now excluded, False = now included
    was_already: bool  # True = no change was needed


class ManageUserExclusions:
    """Use cases for managing per-scope GitHub user exclusions."""

    def __init__(self, exclusion_repo: UserExclusionPort) -> None:
        self._repo = exclusion_repo

    async def exclude_user(self, scope_key: str, github_username: str) -> ExclusionResult:
        """Add a GitHub username to the exclusion list for a scope."""
        added = await self._repo.add(scope_key, github_username)
        return ExclusionResult(
            username=github_username,
            excluded=True,
            was_already=not added,
        )

    async def include_user(self, scope_key: str, github_username: str) -> ExclusionResult:
        """Remove a GitHub username from the exclusion list for a scope."""
        removed = await self._repo.remove(scope_key, github_username)
        return ExclusionResult(
            username=github_username,
            excluded=False,
            was_already=not removed,
        )

    async def list_excluded_users(self, scope_keys: list[str]) -> dict[str, list[str]]:
        """Return excluded GitHub usernames grouped by scope.

        Only scopes with at least one exclusion are returned.
        """
        return await self._repo.list_excluded(scope_keys)


class ManageSelfReviews:
    """Use cases for the per-scope self-review mute flag.

    When the flag is set at a scope, prbot skips the ``commented`` emoji
    reaction if the PR author is the same person who just commented on
    their own PR.
    """

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
