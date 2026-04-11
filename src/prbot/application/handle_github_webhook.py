import logging

from prbot.domain.ports import (
    EmojiConfigResolverPort,
    PRRepositoryPort,
    PRSourcePort,
    ReactionPort,
    UserExclusionPort,
)
from prbot.domain.status_resolver import resolve_pr_status
from prbot.domain.value_objects import EmojiConfig, PRUrl

logger = logging.getLogger(__name__)


class HandleGitHubWebhook:
    """Use case: a source webhook fires, update all tracked messages."""

    def __init__(
        self,
        source: PRSourcePort,
        reactions: ReactionPort,
        pr_repository: PRRepositoryPort,
        emoji_resolver: EmojiConfigResolverPort,
        user_exclusions: UserExclusionPort,
    ) -> None:
        self._source = source
        self._reactions = reactions
        self._repo = pr_repository
        self._emoji_resolver = emoji_resolver
        self._user_exclusions = user_exclusions

    async def execute(
        self,
        owner: str,
        repo: str,
        number: int,
        sender: str | None = None,
    ) -> None:
        """Re-evaluate PR status and add new reactions to all messages tracking it."""
        pr_url = PRUrl(owner=owner, repo=repo, number=number)

        tracked_prs = await self._repo.find_by_pr_url(pr_url)
        if not tracked_prs:
            logger.debug("No tracked messages for %s", pr_url)
            return

        try:
            pr_info = await self._source.fetch_pr_info(pr_url)
        except Exception:
            logger.warning("Failed to fetch PR info for %s, skipping", pr_url)
            return

        status = resolve_pr_status(pr_info)

        # Cache resolved configs to avoid repeated DB queries for identical scope keys
        config_cache: dict[tuple[str, ...], EmojiConfig] = {}

        for tracked in tracked_prs:
            if sender:
                excluded = await self._user_exclusions.is_excluded(list(tracked.scope_keys), sender)
                if excluded:
                    logger.info(
                        "Skipping %s for %s — sender %r is excluded",
                        pr_url,
                        tracked.message_ref,
                        sender,
                    )
                    continue

            cache_key = tracked.scope_keys
            if cache_key not in config_cache:
                config_cache[cache_key] = await self._emoji_resolver.resolve(
                    list(tracked.scope_keys)
                )
            emoji = config_cache[cache_key].for_status(status)

            if emoji is None or tracked.has_emoji(emoji):
                continue

            await self._reactions.add_reaction(tracked.message_ref, emoji)
            await self._repo.add_emoji(pr_url, tracked.message_ref, emoji)
