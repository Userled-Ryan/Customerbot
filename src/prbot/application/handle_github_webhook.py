import logging

from prbot.domain.ports import EmojiConfigResolverPort, PRRepositoryPort, PRSourcePort, ReactionPort
from prbot.domain.status_resolver import resolve_pr_status
from prbot.domain.value_objects import MessageRef, PRUrl

logger = logging.getLogger(__name__)


class HandleGitHubWebhook:
    """Use case: a source webhook fires, update all tracked messages."""

    def __init__(
        self,
        source: PRSourcePort,
        reactions: ReactionPort,
        pr_repository: PRRepositoryPort,
        emoji_resolver: EmojiConfigResolverPort,
    ) -> None:
        self._source = source
        self._reactions = reactions
        self._repo = pr_repository
        self._emoji_resolver = emoji_resolver

    async def execute(self, owner: str, repo: str, number: int) -> None:
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

        for tracked in tracked_prs:
            # Resolve emoji config per-message since each may belong to a different scope
            scope_keys = _scope_keys_from_ref(tracked.message_ref)
            emoji_config = await self._emoji_resolver.resolve(scope_keys)
            emoji = emoji_config.for_status(status)

            if emoji is None or tracked.has_emoji(emoji):
                continue

            await self._reactions.add_reaction(tracked.message_ref, emoji)
            await self._repo.add_emoji(pr_url, tracked.message_ref, emoji)


def _scope_keys_from_ref(message_ref: MessageRef) -> list[str]:
    """Build scope keys from a MessageRef.

    For now, this only provides the integration-level scope key.
    Integrations can store richer scope info in the ref if needed.
    """
    return [message_ref.integration_id]
