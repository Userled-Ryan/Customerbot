import logging

from slack_sdk.web.async_client import AsyncWebClient

from prbot.domain.value_objects import MessageRef

logger = logging.getLogger(__name__)

INTEGRATION_ID = "slack"


def encode_ref(channel: str, ts: str) -> MessageRef:
    """Encode a Slack channel and timestamp into a MessageRef."""
    return MessageRef(integration_id=INTEGRATION_ID, ref=f"{channel}:{ts}")


def decode_ref(message_ref: MessageRef) -> tuple[str, str]:
    """Decode a Slack MessageRef into (channel, timestamp)."""
    channel, ts = message_ref.ref.split(":", 1)
    return channel, ts


class SlackGateway:
    """Concrete adapter: manages Slack emoji reactions via the Slack Web API."""

    def __init__(self, client: AsyncWebClient) -> None:
        self._client = client

    async def add_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        channel, timestamp = decode_ref(message_ref)
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
