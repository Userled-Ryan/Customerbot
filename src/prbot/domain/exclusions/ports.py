from typing import Protocol


class UserExclusionPort(Protocol):
    """Port for managing GitHub username exclusions per scope."""

    async def is_excluded(self, scope_keys: list[str], github_username: str) -> bool:
        """Check if a username is excluded in any of the given scopes (most-specific first)."""
        ...

    async def add(self, scope_key: str, github_username: str) -> bool:
        """Exclude a user. Returns False if already excluded."""
        ...

    async def remove(self, scope_key: str, github_username: str) -> bool:
        """Re-include a user. Returns False if not currently excluded."""
        ...

    async def list_excluded(self, scope_keys: list[str]) -> dict[str, list[str]]:
        """Return excluded usernames grouped by scope.

        Result contains an entry for each scope in *scope_keys* that has at
        least one exclusion. Scopes with none are omitted.
        """
        ...
