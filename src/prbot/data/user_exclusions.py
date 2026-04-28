from __future__ import annotations

import logging

from prbot.domain.common.ports import ScopeSettingsPort

logger = logging.getLogger(__name__)

EXCLUDED_USERS_SETTING_KEY = "excluded_users"


def _as_usernames(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [u for u in value if isinstance(u, str)]


class SQLiteUserExclusionRepository:
    """Stores per-scope GitHub username exclusions in the generic scope_settings store.

    Exclusions for a given scope are stored as a JSON array of usernames under
    the ``excluded_users`` key. Resolution walks from most-specific to
    least-specific — a username excluded at *any* matching scope is considered
    excluded.
    """

    def __init__(self, settings: ScopeSettingsPort) -> None:
        self._settings = settings

    async def is_excluded(self, scope_keys: list[str], github_username: str) -> bool:
        if not scope_keys:
            return False

        lower = github_username.lower()
        grouped = await self._settings.get_all_at(scope_keys, EXCLUDED_USERS_SETTING_KEY)
        return any(
            any(u.lower() == lower for u in _as_usernames(usernames))
            for usernames in grouped.values()
        )

    async def add(self, scope_key: str, github_username: str) -> bool:
        current = await self._load_scope(scope_key)
        if github_username in current:
            return False
        await self._settings.set(scope_key, EXCLUDED_USERS_SETTING_KEY, [*current, github_username])
        logger.info("Excluded user %r in scope %s", github_username, scope_key)
        return True

    async def remove(self, scope_key: str, github_username: str) -> bool:
        current = await self._load_scope(scope_key)
        if github_username not in current:
            return False
        updated = [u for u in current if u != github_username]
        if updated:
            await self._settings.set(scope_key, EXCLUDED_USERS_SETTING_KEY, updated)
        else:
            await self._settings.unset(scope_key, EXCLUDED_USERS_SETTING_KEY)
        logger.info("Re-included user %r in scope %s", github_username, scope_key)
        return True

    async def list_excluded(self, scope_keys: list[str]) -> dict[str, list[str]]:
        grouped = await self._settings.get_all_at(scope_keys, EXCLUDED_USERS_SETTING_KEY)
        return {
            scope: usernames for scope, raw in grouped.items() if (usernames := _as_usernames(raw))
        }

    async def excluded_logins(self, scope_keys: list[str]) -> set[str]:
        if not scope_keys:
            return set()
        grouped = await self._settings.get_all_at(scope_keys, EXCLUDED_USERS_SETTING_KEY)
        return {u.lower() for raw in grouped.values() for u in _as_usernames(raw)}

    async def _load_scope(self, scope_key: str) -> list[str]:
        raw = await self._settings.get([scope_key], EXCLUDED_USERS_SETTING_KEY)
        return _as_usernames(raw)
