import logging

from prbot.application.ports import GitHubClientPort, PRRepositoryPort, ReactionPort
from prbot.config import EmojiConfig
from prbot.domain.status_resolver import resolve_pr_status
from prbot.domain.value_objects import PRUrl

logger = logging.getLogger(__name__)


class HandleGitHubWebhook:
    """Use case: GitHub webhook fires, update all tracked messages."""

    def __init__(
        self,
        github_client: GitHubClientPort,
        reactions: ReactionPort,
        pr_repository: PRRepositoryPort,
        emoji_config: EmojiConfig,
    ) -> None:
        self._github = github_client
        self._reactions = reactions
        self._repo = pr_repository
        self._emoji_config = emoji_config

    async def execute(self, owner: str, repo: str, number: int) -> None:
        """Re-evaluate PR status and add new reactions to all messages tracking it."""
        pr_url = PRUrl(owner=owner, repo=repo, number=number)

        tracked_prs = await self._repo.find_by_pr_url(pr_url)
        if not tracked_prs:
            logger.debug("No tracked messages for %s", pr_url.full_url)
            return

        try:
            pr_info = await self._github.fetch_pr_info(pr_url)
        except Exception:
            logger.warning("Failed to fetch PR info for %s, skipping", pr_url.full_url)
            return

        status = resolve_pr_status(pr_info)
        emoji = self._emoji_config.for_status(status)

        if emoji is None:
            return

        for tracked in tracked_prs:
            if tracked.has_emoji(emoji):
                continue

            await self._reactions.add_reaction(tracked.message_ref, emoji)
            await self._repo.add_emoji(pr_url, tracked.message_ref, emoji)
