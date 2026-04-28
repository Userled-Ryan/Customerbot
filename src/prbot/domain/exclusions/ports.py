from dataclasses import dataclass
from typing import Literal, Protocol

GitHubUserKind = Literal["user", "bot", "organization"]


@dataclass(frozen=True)
class GitHubUserRef:
    """A resolved GitHub account, as returned by the users API."""

    login: str  # canonical login returned by GitHub (case normalized by GitHub)
    kind: GitHubUserKind


class GitHubUserLookupPort(Protocol):
    """Port for resolving a GitHub login to its account kind (user / bot / org)."""

    async def lookup_user(self, github_username: str) -> GitHubUserRef | None:
        """Look up a GitHub account by login.

        Returns None if the account does not exist. For GitHub App bots, callers
        may pass the webhook-form login ``<name>[bot]`` — implementations strip
        the ``[bot]`` suffix before querying.
        """
        ...


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

    async def excluded_logins(self, scope_keys: list[str]) -> set[str]:
        """Return all excluded GitHub logins (lowercased) across the scope chain.

        Use to filter a list of usernames in bulk — e.g. dropping reviews from
        excluded reviewers before resolving PR status.
        """
        ...
