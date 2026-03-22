from collections.abc import Sequence

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import PRInfo, PRUrl


class FakeGitHubClient:
    def __init__(self, pr_info: PRInfo) -> None:
        self._pr_info = pr_info

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        return self._pr_info


class FakeSlackReactions:
    def __init__(self) -> None:
        self.added: list[tuple[str, str, str]] = []

    async def add_reaction(self, channel: str, timestamp: str, emoji: str) -> None:
        self.added.append((channel, timestamp, emoji))


class FakePRRepository:
    def __init__(self) -> None:
        self.stored: list[TrackedPR] = []

    async def save(self, tracked_pr: TrackedPR) -> None:
        self.stored.append(tracked_pr)

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]:
        return [t for t in self.stored if t.pr_url == pr_url]

    async def add_emoji(
        self,
        pr_url: PRUrl,
        channel_id: str,
        message_ts: str,
        emoji: str,
    ) -> None:
        for i, t in enumerate(self.stored):
            if t.pr_url == pr_url and t.channel_id == channel_id and t.message_ts == message_ts:
                self.stored[i] = t.with_added_emoji(emoji)
