from collections.abc import Sequence
from typing import Protocol

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import PRInfo, PRUrl


class GitHubClientPort(Protocol):
    """Port for fetching PR data from GitHub."""

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo: ...


class SlackReactionPort(Protocol):
    """Port for managing Slack emoji reactions."""

    async def add_reaction(self, channel: str, timestamp: str, emoji: str) -> None: ...


class PRRepositoryPort(Protocol):
    """Port for persisting tracked PRs."""

    async def save(self, tracked_pr: TrackedPR) -> None: ...

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]: ...

    async def add_emoji(
        self,
        pr_url: PRUrl,
        channel_id: str,
        message_ts: str,
        emoji: str,
    ) -> None: ...
