from __future__ import annotations

import logging
import time
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from customerbot.domain.messaging.ports import ThreadMessage

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

    async def send_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        try:
            if thread_ts:
                await self._client.chat_postMessage(
                    channel=channel_id, text=text, thread_ts=thread_ts
                )
            else:
                await self._client.chat_postMessage(channel=channel_id, text=text)
        except Exception:
            logger.exception("Failed to send message to %s", channel_id)

    async def send_ephemeral(self, channel_id: str, user_id: str, text: str) -> None:
        """Send a message visible only to user_id — leaves no trace in the channel."""
        try:
            await self._client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)
        except Exception:
            logger.exception("Failed to send ephemeral message to %s in %s", user_id, channel_id)

    async def send_ephemeral_blocks(
        self,
        channel_id: str,
        user_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> None:
        """Ephemeral Block-Kit message — visible only to user_id."""
        try:
            await self._client.chat_postEphemeral(
                channel=channel_id, user=user_id, blocks=blocks, text=text
            )
        except Exception:
            logger.exception("Failed to send ephemeral blocks to %s in %s", user_id, channel_id)

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

    async def get_message_text(self, channel_id: str, ts: str) -> str:
        resp = await self._client.conversations_history(
            channel=channel_id,
            latest=ts,
            inclusive=True,
            limit=1,
        )
        messages = resp.get("messages") or []
        if not messages:
            return ""
        return str(messages[0].get("text", ""))

    def build_thread_link(self, channel_id: str, thread_ts: str) -> str:
        return build_thread_link(self._workspace_url, channel_id, thread_ts)

    async def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> str | None:
        try:
            resp = await self._client.chat_postMessage(channel=channel_id, blocks=blocks, text=text)
            ts = resp.get("ts")
            return str(ts) if ts else None
        except Exception:
            logger.exception("Failed to post blocks to %s", channel_id)
            return None

    async def update_message(
        self,
        channel_id: str,
        message_ts: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> None:
        try:
            await self._client.chat_update(
                channel=channel_id, ts=message_ts, blocks=blocks, text=text
            )
        except Exception:
            logger.exception("Failed to update message %s:%s", channel_id, message_ts)

    async def open_view(self, trigger_id: str, view: dict[str, Any]) -> str | None:
        try:
            resp = await self._client.views_open(trigger_id=trigger_id, view=view)
            view_data = resp.get("view") or {}
            view_id = view_data.get("id")
            return str(view_id) if view_id else None
        except Exception:
            logger.exception("Failed to open view via trigger_id=%s", trigger_id)
            return None

    async def update_view(self, view_id: str, view: dict[str, Any]) -> None:
        try:
            await self._client.views_update(view_id=view_id, view=view)
        except Exception:
            logger.exception("Failed to update view %s", view_id)

    async def send_dm_blocks(
        self,
        user_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> tuple[str, str] | None:
        try:
            resp = await self._client.conversations_open(users=user_id)
            dm_channel = str(resp["channel"]["id"])
            posted = await self._client.chat_postMessage(
                channel=dm_channel, blocks=blocks, text=text
            )
            ts = posted.get("ts")
            if ts is None:
                return None
            return (dm_channel, str(ts))
        except Exception:
            logger.exception("Failed to send DM blocks to %s", user_id)
            return None

    async def is_user_in_group(self, user_id: str, group_id: str) -> bool:
        try:
            resp = await self._client.usergroups_users_list(usergroup=group_id)
            users = resp.get("users") or []
            return user_id in users
        except Exception:
            logger.exception("Failed to read members of usergroup %s; failing closed", group_id)
            return False

    async def get_thread_messages(
        self, channel_id: str, thread_ts: str, *, limit: int = 5
    ) -> list[ThreadMessage]:
        try:
            resp = await self._client.conversations_replies(
                channel=channel_id, ts=thread_ts, limit=max(limit, 1)
            )
            raw = resp.get("messages") or []
        except Exception:
            logger.exception(
                "Failed to fetch thread %s:%s — returning empty context",
                channel_id,
                thread_ts,
            )
            return []
        # Most-recent N. Slack returns oldest-first.
        recent = raw[-limit:]
        return [
            ThreadMessage(user_id=str(m.get("user", "")), text=str(m.get("text", "")))
            for m in recent
        ]
