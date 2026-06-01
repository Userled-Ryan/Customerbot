from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ThreadMessage(BaseModel, frozen=True):
    """One Slack message in a thread, as returned by `get_thread_messages`."""

    user_id: str
    text: str


class SlackPort(Protocol):
    """v1 Slack abstraction.

    Distinct from the legacy `MessengerPort` in `domain.tracking.ports` — that
    one is text-only and tied to the tracking-conversation use cases. This port
    adds view (modal) and block-message support needed by the v1 ticket flow.
    """

    async def send_dm(self, user_id: str, text: str) -> None: ...

    async def send_dm_blocks(
        self,
        user_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> tuple[str, str] | None:
        """Open a DM with the user and post a Block-Kit message.

        Returns `(dm_channel_id, message_ts)` on success — both are needed to
        chat.update the message later when SE clicks the interactive button.
        """
        ...

    async def send_message(
        self,
        channel_id: str,
        text: str,
        thread_ts: str | None = None,
    ) -> None: ...

    async def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> str | None:
        """Post a Block-Kit message. Returns the message ts on success."""
        ...

    async def update_message(
        self,
        channel_id: str,
        message_ts: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> None: ...

    async def open_view(self, trigger_id: str, view: dict[str, Any]) -> str | None:
        """Open a modal view. Returns the Slack view_id on success."""
        ...

    async def get_channel_name(self, channel_id: str) -> str: ...

    async def is_user_in_group(self, user_id: str, group_id: str) -> bool:
        """True if `user_id` is a current member of the Slack user-group `group_id`."""
        ...

    async def get_thread_messages(
        self, channel_id: str, thread_ts: str, *, limit: int = 5
    ) -> list[ThreadMessage]:
        """Return up to `limit` most-recent messages from a thread."""
        ...

    def build_thread_link(self, channel_id: str, thread_ts: str) -> str: ...
