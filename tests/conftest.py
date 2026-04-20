import re
from collections.abc import Sequence

from prbot.domain.emoji.value_objects import EmojiConfig
from prbot.domain.exclusions.ports import GitHubUserKind, GitHubUserRef
from prbot.domain.tracking.entities import TrackedPR
from prbot.domain.tracking.value_objects import MessageRef, PRInfo, PRUrl

_GITHUB_PR_PATTERN = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")


class FakePRSource:
    def __init__(self, pr_info: PRInfo) -> None:
        self._pr_info = pr_info

    def extract_pr_references(self, text: str) -> list[PRUrl]:
        seen: set[tuple[str, str, int]] = set()
        results: list[PRUrl] = []
        for match in _GITHUB_PR_PATTERN.finditer(text):
            key = (match.group(1), match.group(2), int(match.group(3)))
            if key not in seen:
                seen.add(key)
                results.append(PRUrl(owner=key[0], repo=key[1], number=key[2]))
        return results

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        return self._pr_info


class FakeReactions:
    def __init__(self) -> None:
        self.added: list[tuple[MessageRef, str]] = []
        self.added_with_fallback: list[tuple[MessageRef, str, str | None]] = []

    async def add_reaction(
        self,
        message_ref: MessageRef,
        emoji: str,
        fallback_emoji: str | None = None,
    ) -> None:
        self.added.append((message_ref, emoji))
        self.added_with_fallback.append((message_ref, emoji, fallback_emoji))


class FakePRRepository:
    def __init__(self) -> None:
        self.stored: list[TrackedPR] = []

    async def save(self, tracked_pr: TrackedPR) -> None:
        self.stored.append(tracked_pr)

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]:
        return [t for t in self.stored if t.pr_url == pr_url]

    async def find_distinct_pr_urls(self) -> Sequence[PRUrl]:
        seen: set[PRUrl] = set()
        results: list[PRUrl] = []
        for t in self.stored:
            if t.pr_url not in seen:
                seen.add(t.pr_url)
                results.append(t.pr_url)
        return results

    async def add_emoji(
        self,
        pr_url: PRUrl,
        message_ref: MessageRef,
        emoji: str,
    ) -> None:
        for i, t in enumerate(self.stored):
            if t.pr_url == pr_url and t.message_ref == message_ref:
                self.stored[i] = t.with_added_emoji(emoji)


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


class FakeEmojiConfigResolver:
    """Fake resolver that always returns the given EmojiConfig."""

    def __init__(self, config: EmojiConfig | None = None) -> None:
        self._config = config or EmojiConfig()

    async def resolve(self, scope_keys: list[str]) -> EmojiConfig:
        return self._config


class FakeScopeSettingsRepo:
    """In-memory ScopeSettingsPort for tests."""

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
    """In-memory user exclusion repository for testing."""

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


class FakeGitHubUserLookup:
    """In-memory GitHub user lookup for tests.

    Seed with ``ref(login, kind)`` entries; any login not present returns None.
    Use ``raise_for(login)`` to simulate a transient API failure.
    """

    def __init__(self, refs: dict[str, GitHubUserRef] | None = None) -> None:
        # keys are lowercased
        self._refs: dict[str, GitHubUserRef] = {k.lower(): v for k, v in (refs or {}).items()}
        self._raise_for: set[str] = set()

    def seed(self, login: str, kind: GitHubUserKind) -> None:
        self._refs[login.lower()] = GitHubUserRef(login=login, kind=kind)

    def raise_for(self, login: str) -> None:
        self._raise_for.add(login.lower())

    async def lookup_user(self, github_username: str) -> GitHubUserRef | None:
        key = github_username.strip()
        if key.lower().endswith("[bot]"):
            key = key[: -len("[bot]")]
        key_l = key.lower()
        if key_l in self._raise_for:
            raise RuntimeError("simulated GitHub lookup failure")
        return self._refs.get(key_l)
