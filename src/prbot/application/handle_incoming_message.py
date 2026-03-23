import logging
import re

from prbot.application.ports import GitHubClientPort, PRRepositoryPort, ReactionPort
from prbot.config import EmojiConfig
from prbot.domain.entities import TrackedPR
from prbot.domain.status_resolver import resolve_pr_status
from prbot.domain.value_objects import MessageRef, PRUrl

logger = logging.getLogger(__name__)

_PR_URL_PATTERN = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")


class HandleIncomingMessage:
    """Use case: a new message arrives from a messaging platform, check for PR URLs."""

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

    async def execute(self, message_ref: MessageRef, text: str) -> None:
        """Process a message, find PR URLs, fetch status, react."""
        pr_urls = self._extract_pr_urls(text)

        for pr_url in pr_urls:
            try:
                pr_info = await self._github.fetch_pr_info(pr_url)
            except Exception:
                logger.warning("Failed to fetch PR info for %s, skipping", pr_url.full_url)
                continue

            status = resolve_pr_status(pr_info)
            emoji = self._emoji_config.for_status(status)

            tracked = TrackedPR(
                pr_url=pr_url,
                message_ref=message_ref,
            )

            if emoji is not None:
                await self._reactions.add_reaction(message_ref, emoji)
                tracked = tracked.with_added_emoji(emoji)

            await self._repo.save(tracked)

    @staticmethod
    def _extract_pr_urls(text: str) -> list[PRUrl]:
        """Extract all unique PR URLs from message text."""
        seen: set[tuple[str, str, int]] = set()
        results: list[PRUrl] = []
        for match in _PR_URL_PATTERN.finditer(text):
            key = (match.group(1), match.group(2), int(match.group(3)))
            if key not in seen:
                seen.add(key)
                results.append(PRUrl(owner=key[0], repo=key[1], number=key[2]))
        return results
