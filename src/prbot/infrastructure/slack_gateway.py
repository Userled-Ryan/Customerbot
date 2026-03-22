import logging

from slack_sdk.web.async_client import AsyncWebClient

from prbot.domain.value_objects import EmojiReaction

logger = logging.getLogger(__name__)


class SlackGateway:
    """Concrete adapter: manages Slack emoji reactions via the Slack Web API."""

    def __init__(self, client: AsyncWebClient) -> None:
        self._client = client

    async def add_reaction(self, channel: str, timestamp: str, emoji: EmojiReaction) -> None:
        try:
            await self._client.reactions_add(
                channel=channel,
                timestamp=timestamp,
                name=emoji.value,
            )
        except Exception as exc:
            if "already_reacted" in str(exc):
                logger.debug("Already reacted with %s", emoji.value)
            else:
                raise

    async def remove_reaction(self, channel: str, timestamp: str, emoji: EmojiReaction) -> None:
        try:
            await self._client.reactions_remove(
                channel=channel,
                timestamp=timestamp,
                name=emoji.value,
            )
        except Exception as exc:
            if "no_reaction" in str(exc):
                logger.debug("No reaction %s to remove", emoji.value)
            else:
                raise
