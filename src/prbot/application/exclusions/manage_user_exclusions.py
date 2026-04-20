"""Application-layer operations for managing GitHub user exclusions per scope."""

from dataclasses import dataclass

from prbot.domain.exclusions.ports import (
    GitHubUserLookupPort,
    GitHubUserRef,
    UserExclusionPort,
)


@dataclass(frozen=True)
class ExclusionResult:
    """Outcome of an exclude/include operation."""

    username: str
    excluded: bool  # True = now excluded, False = now included
    was_already: bool  # True = no change was needed
    lookup: GitHubUserRef | None = None  # GitHub account at the time of the op (None=not found)
    lookup_failed: bool = False  # True if the lookup errored (advisory result unavailable)


@dataclass(frozen=True)
class ExclusionEntry:
    """A stored exclusion paired with its current GitHub account lookup."""

    username: str
    lookup: GitHubUserRef | None
    lookup_failed: bool = False


class ManageUserExclusions:
    """Use cases for managing per-scope GitHub user exclusions."""

    def __init__(
        self,
        exclusion_repo: UserExclusionPort,
        github_lookup: GitHubUserLookupPort | None = None,
    ) -> None:
        self._repo = exclusion_repo
        self._lookup = github_lookup

    async def exclude_user(self, scope_key: str, github_username: str) -> ExclusionResult:
        """Add a GitHub username to the exclusion list for a scope.

        Also resolves the login against GitHub (if a lookup port is available)
        so callers can surface validation hints to the user.
        """
        lookup, failed = await self._resolve(github_username)
        added = await self._repo.add(scope_key, github_username)
        return ExclusionResult(
            username=github_username,
            excluded=True,
            was_already=not added,
            lookup=lookup,
            lookup_failed=failed,
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

    async def check_excluded_users(self, scope_keys: list[str]) -> dict[str, list[ExclusionEntry]]:
        """Return excluded usernames grouped by scope, each paired with a live GitHub lookup."""
        grouped = await self._repo.list_excluded(scope_keys)
        result: dict[str, list[ExclusionEntry]] = {}
        for scope_key, users in grouped.items():
            entries: list[ExclusionEntry] = []
            for username in users:
                lookup, failed = await self._resolve(username)
                entries.append(
                    ExclusionEntry(username=username, lookup=lookup, lookup_failed=failed)
                )
            result[scope_key] = entries
        return result

    async def _resolve(self, github_username: str) -> tuple[GitHubUserRef | None, bool]:
        """Look up a login; returns (ref-or-None, failed-flag)."""
        if self._lookup is None:
            return None, False
        try:
            return await self._lookup.lookup_user(github_username), False
        except Exception:
            return None, True
