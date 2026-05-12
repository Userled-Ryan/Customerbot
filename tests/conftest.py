class FakeCursorRepo:
    def __init__(self) -> None:
        self.cursors: dict[tuple[str, str], str] = {}

    async def get_cursor(self, integration_id: str, channel_id: str) -> str | None:
        return self.cursors.get((integration_id, channel_id))

    async def upsert_cursor(self, integration_id: str, channel_id: str, ts: str) -> None:
        key = (integration_id, channel_id)
        existing = self.cursors.get(key)
        if existing is None or ts > existing:
            self.cursors[key] = ts


class FakeScopeSettingsRepo:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], object] = {}

    async def get(self, scope_keys: list[str], key: str) -> object | None:
        for scope_key in scope_keys:
            if (scope_key, key) in self._store:
                return self._store[(scope_key, key)]
        return None

    async def get_all_at(self, scope_keys: list[str], key: str) -> dict[str, object]:
        return {
            scope_key: self._store[(scope_key, key)]
            for scope_key in scope_keys
            if (scope_key, key) in self._store
        }

    async def set(self, scope_key: str, key: str, value: object) -> None:
        self._store[(scope_key, key)] = value

    async def unset(self, scope_key: str, key: str) -> bool:
        return self._store.pop((scope_key, key), None) is not None


class FakeUserExclusionRepo:
    def __init__(self) -> None:
        self._exclusions: dict[str, set[str]] = {}

    async def is_excluded(self, scope_keys: list[str], github_username: str) -> bool:
        lower = github_username.lower()
        for key in scope_keys:
            if key in self._exclusions and any(u.lower() == lower for u in self._exclusions[key]):
                return True
        return False

    async def add(self, scope_key: str, github_username: str) -> bool:
        exclusions = self._exclusions.setdefault(scope_key, set())
        if github_username in exclusions:
            return False
        exclusions.add(github_username)
        return True

    async def remove(self, scope_key: str, github_username: str) -> bool:
        exclusions = self._exclusions.get(scope_key, set())
        if github_username not in exclusions:
            return False
        exclusions.discard(github_username)
        return True

    async def list_excluded(self, scope_keys: list[str]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for key in scope_keys:
            if key in self._exclusions and self._exclusions[key]:
                grouped[key] = sorted(self._exclusions[key])
        return grouped

    async def excluded_logins(self, scope_keys: list[str]) -> set[str]:
        return {u.lower() for key in scope_keys for u in self._exclusions.get(key, set())}
