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

    async def send_ephemeral(self, channel_id: str, user_id: str, text: str) -> None:
        """Send a message visible only to `user_id` — leaves no channel trace."""
        ...

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

    async def update_view(self, view_id: str, view: dict[str, Any]) -> None:
        """Replace an already-open modal view in place (Slack `views.update`)."""
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

    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> None:
        """Add an emoji reaction (name without colons) to a message. Best-effort."""
        ...

    async def remove_reaction(self, channel_id: str, ts: str, emoji: str) -> None:
        """Remove an emoji reaction (name without colons) from a message. Best-effort."""
        ...

    def build_thread_link(self, channel_id: str, thread_ts: str) -> str: ...

    def parse_thread_link(self, link: str) -> tuple[str, str] | None:
        """Inverse of `build_thread_link`: recover `(channel_id, thread_ts)` from
        a thread permalink, or None if the link isn't in that shape."""
        ...
