from typing import Protocol


class ScopeSettingsPort(Protocol):
    """Port for a key/value-per-scope settings store.

    Values are JSON-serializable. Scope keys are ordered most-specific first;
    ``get`` returns the first scope's value, while ``get_all_at`` surfaces
    every matching scope for features that combine values.

    This is a generic primitive used across multiple sub-domains
    (e.g. the self-review mute flag under ``exclusions`` and custom emoji
    overrides under ``emoji``).
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
