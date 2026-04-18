import re
from collections.abc import Sequence

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import EmojiConfig, MessageRef, PRInfo, PRUrl

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

    async def add_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        self.added.append((message_ref, emoji))


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
