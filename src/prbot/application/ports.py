from collections.abc import Sequence
from typing import Protocol

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import MessageRef, PRInfo, PRUrl


class GitHubClientPort(Protocol):
    """Port for fetching PR data from GitHub."""

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo: ...


class ReactionPort(Protocol):
    """Port for adding emoji reactions to messages in any messaging platform."""

    async def add_reaction(self, message_ref: MessageRef, emoji: str) -> None: ...


class PRRepositoryPort(Protocol):
    """Port for persisting tracked PRs."""

    async def save(self, tracked_pr: TrackedPR) -> None: ...

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]: ...

    async def add_emoji(
        self,
        pr_url: PRUrl,
        message_ref: MessageRef,
        emoji: str,
    ) -> None: ...
