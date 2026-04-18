"""Application-layer operations for managing user exclusions.

Each public method is a self-contained use case (command or query) that
operates on the domain model.  Integration layers (Slack slash commands,
Discord bot commands, future REST API, …) call these and render the
result in their own format.
"""

import logging
from dataclasses import dataclass

from prbot.domain.ports import UserExclusionPort

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
