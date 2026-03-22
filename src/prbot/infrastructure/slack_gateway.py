import logging

from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


class SlackGateway:
    """Concrete adapter: manages Slack emoji reactions via the Slack Web API."""

    def __init__(self, client: AsyncWebClient) -> None:
        self._client = client

    async def add_reaction(self, channel: str, timestamp: str, emoji: str) -> None:
        try:
            await self._client.reactions_add(
                channel=channel,
                timestamp=timestamp,
                name=emoji,
            )
        except Exception as exc:
            if "already_reacted" in str(exc):
                logger.debug("Already reacted with %s", emoji)
            else:
                raise
