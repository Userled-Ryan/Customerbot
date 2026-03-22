import logging

from prbot.application.ports import GitHubClientPort, PRRepositoryPort, SlackReactionPort
from prbot.domain.status_resolver import resolve_pr_status
from prbot.domain.value_objects import EmojiReaction, PRUrl

logger = logging.getLogger(__name__)


class HandleGitHubWebhook:
    """Use case: GitHub webhook fires, update all tracked messages."""

    def __init__(
        self,
        github_client: GitHubClientPort,
        slack_reactions: SlackReactionPort,
        pr_repository: PRRepositoryPort,
    ) -> None:
        self._github = github_client
        self._slack = slack_reactions
        self._repo = pr_repository

    async def execute(self, owner: str, repo: str, number: int) -> None:
        """Re-evaluate PR status and update all Slack messages tracking it."""
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
        new_emoji = EmojiReaction.from_status(status)

        for tracked in tracked_prs:
            if not tracked.needs_update(new_emoji):
                continue

            if tracked.current_emoji is not None:
                try:
                    await self._slack.remove_reaction(
                        tracked.channel_id, tracked.message_ts, tracked.current_emoji
                    )
                except Exception:
                    logger.warning(
                        "Failed to remove reaction %s from %s/%s",
                        tracked.current_emoji,
                        tracked.channel_id,
                        tracked.message_ts,
                    )

            await self._slack.add_reaction(tracked.channel_id, tracked.message_ts, new_emoji)

            await self._repo.update_emoji(pr_url, tracked.channel_id, tracked.message_ts, new_emoji)
