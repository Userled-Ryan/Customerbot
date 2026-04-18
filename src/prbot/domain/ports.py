from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import EmojiConfig, MessageRef, PRInfo, PRUrl

# --- Input source ports ---


class PRSourcePort(Protocol):
    """Port for an input source that can recognise and fetch PR references.

    Each implementation (GitHub, GitLab, …) knows its own URL patterns
    and how to retrieve PR metadata from its API.
    """

    def extract_pr_references(self, text: str) -> list[PRUrl]:
        """Return all PR references found in *text* that this source understands."""
        ...

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        """Fetch current state for a recognised PR reference."""
        ...


# --- Output bot ports ---


class ReactionPort(Protocol):
    """Port for adding emoji reactions to messages in any messaging platform."""

    async def add_reaction(self, message_ref: MessageRef, emoji: str) -> None: ...


# --- Repository ports ---


@runtime_checkable
class PRRepositoryPort(Protocol):
    """Port for persisting tracked PRs."""

    async def save(self, tracked_pr: TrackedPR) -> None: ...

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]: ...

    async def find_distinct_pr_urls(self) -> Sequence[PRUrl]: ...

    async def add_emoji(
        self,
        pr_url: PRUrl,
        message_ref: MessageRef,
        emoji: str,
    ) -> None: ...


# --- Channel cursor ports ---


class ChannelCursorPort(Protocol):
    """Port for tracking the last-seen message timestamp per channel."""

    async def get_cursor(self, integration_id: str, channel_id: str) -> str | None: ...

    async def upsert_cursor(self, integration_id: str, channel_id: str, ts: str) -> None:
        """Advance the cursor. Must be monotonic — never moves backward."""
        ...


# --- Config resolution ports ---


class EmojiConfigResolverPort(Protocol):
    """Port for resolving emoji config based on scope keys (most-specific first).

    Implementations walk the scope_keys list and return the first matching
    EmojiConfig, falling back to the global default.
    """

    async def resolve(self, scope_keys: list[str]) -> EmojiConfig: ...


class ScopeSettingsPort(Protocol):
    """Port for a key/value-per-scope settings store.

    Values are JSON-serializable. Scope keys are ordered most-specific first;
    ``get`` returns the first scope's value, while ``get_all_at`` surfaces
    every matching scope for features that combine values (e.g. exclusions).
    """

    async def get(self, scope_keys: list[str], key: str) -> object | None:
        """Return the value from the most-specific scope with this key set, or None."""
        ...

    async def get_all_at(self, scope_keys: list[str], key: str) -> dict[str, object]:
        """Return values for this key across the given scopes, grouped by scope.

        Scopes with no value for the key are omitted.
        """
        ...

    async def set(self, scope_key: str, key: str, value: object) -> None:
        """Upsert a setting at a specific scope."""
        ...

    async def unset(self, scope_key: str, key: str) -> bool:
        """Remove a setting at a specific scope. Returns True if a row was deleted."""
        ...


class UserExclusionPort(Protocol):
    """Port for managing GitHub username exclusions per scope.

    Each config domain owns its own storage — the scope key is the
    shared concept they all reference.
    """

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
