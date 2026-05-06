from __future__ import annotations

import logging
import time

from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

INTEGRATION_ID = "slack"


def build_thread_link(workspace_url: str, channel_id: str, thread_ts: str) -> str:
    """Build a deep link to a Slack thread."""
    ts_clean = thread_ts.replace(".", "")
    return f"{workspace_url.rstrip('/')}/archives/{channel_id}/p{ts_clean}"


def seed_cursor() -> str:
    return f"{time.time():.6f}"


class SlackGateway:
    """Adapter: wraps the Slack Web API for sending messages and fetching metadata."""

    def __init__(self, client: AsyncWebClient, workspace_url: str = "") -> None:
        self._client = client
        self._workspace_url = workspace_url
        self._channel_name_cache: dict[str, str] = {}

    async def send_dm(self, user_id: str, text: str) -> None:
        """Open a DM with a user and send a message."""
        try:
            resp = await self._client.conversations_open(users=user_id)
            dm_channel = resp["channel"]["id"]
            await self._client.chat_postMessage(channel=dm_channel, text=text)
        except Exception:
            logger.exception("Failed to send DM to %s", user_id)

    async def send_message(
        self, channel_id: str, text: str, thread_ts: str | None = None
    ) -> None:
        try:
            kwargs: dict[str, object] = {"channel": channel_id, "text": text}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            await self._client.chat_postMessage(**kwargs)
        except Exception:
            logger.exception("Failed to send message to %s", channel_id)

    async def send_ephemeral(self, channel_id: str, user_id: str, text: str) -> None:
        """Send a message visible only to user_id — leaves no trace in the channel."""
        try:
            await self._client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)
        except Exception:
            logger.exception("Failed to send ephemeral message to %s in %s", user_id, channel_id)

    async def get_channel_name(self, channel_id: str) -> str:
        if channel_id in self._channel_name_cache:
            return self._channel_name_cache[channel_id]
        try:
            resp = await self._client.conversations_info(channel=channel_id)
            name: str = resp["channel"].get("name", channel_id)
            self._channel_name_cache[channel_id] = name
            return name
        except Exception:
            logger.warning("Could not fetch name for channel %s", channel_id)
            return channel_id

    def build_thread_link(self, channel_id: str, thread_ts: str) -> str:
        return build_thread_link(self._workspace_url, channel_id, thread_ts)
