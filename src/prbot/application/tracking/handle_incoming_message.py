import logging
from collections.abc import Sequence

from prbot.domain.emoji.ports import EmojiConfigResolverPort
from prbot.domain.tracking.entities import TrackedPR
from prbot.domain.tracking.ports import PRRepositoryPort, PRSourcePort, ReactionPort
from prbot.domain.tracking.status_resolver import resolve_pr_status
from prbot.domain.tracking.value_objects import MessageRef

logger = logging.getLogger(__name__)


class HandleIncomingMessage:
    """Use case: a new message arrives from a messaging platform, check for PR URLs."""

    def __init__(
        self,
        sources: Sequence[PRSourcePort],
        reactions: ReactionPort,
        pr_repository: PRRepositoryPort,
        emoji_resolver: EmojiConfigResolverPort,
    ) -> None:
        self._sources = sources
        self._reactions = reactions
        self._repo = pr_repository
        self._emoji_resolver = emoji_resolver

    async def execute(
        self,
        message_ref: MessageRef,
        text: str,
        scope_keys: list[str] | None = None,
    ) -> None:
        """Process a message, find PR URLs via all registered sources, fetch status, react."""
        resolved_keys = tuple(scope_keys or [])
        emoji_config = await self._emoji_resolver.resolve(list(resolved_keys))

        for source in self._sources:
            pr_urls = source.extract_pr_references(text)

            for pr_url in pr_urls:
                try:
                    pr_info = await source.fetch_pr_info(pr_url)
                except Exception:
                    logger.warning("Failed to fetch PR info for %s, skipping", pr_url)
                    continue

                status = resolve_pr_status(pr_info)
                emoji = emoji_config.for_status(status)

                tracked = TrackedPR(
                    pr_url=pr_url,
                    message_ref=message_ref,
                    scope_keys=resolved_keys,
                )

                if emoji is not None:
                    await self._reactions.add_reaction(message_ref, emoji)
                    tracked = tracked.with_added_emoji(emoji)

                await self._repo.save(tracked)
