from collections.abc import Sequence

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import MessageRef, PRInfo, PRUrl


class FakeGitHubClient:
    def __init__(self, pr_info: PRInfo) -> None:
        self._pr_info = pr_info

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

    async def add_emoji(
        self,
        pr_url: PRUrl,
        message_ref: MessageRef,
        emoji: str,
    ) -> None:
        for i, t in enumerate(self.stored):
            if t.pr_url == pr_url and t.message_ref == message_ref:
                self.stored[i] = t.with_added_emoji(emoji)
