from __future__ import annotations

from datetime import datetime
from typing import Protocol

from customerbot.domain.tracking.entities import TrackedConversation
from customerbot.domain.tracking.value_objects import ConversationStatus


class ConversationRepositoryPort(Protocol):
    async def upsert(self, conversation: TrackedConversation) -> None: ...

    async def find_by_thread(
        self, channel_id: str, thread_ts: str
    ) -> TrackedConversation | None: ...

    async def find_by_id(self, ticket_id: int) -> TrackedConversation | None: ...

    async def find_open(self) -> list[TrackedConversation]: ...

    async def find_overdue(self, hours: int) -> list[TrackedConversation]: ...

    async def update_last_reply(
        self, channel_id: str, thread_ts: str, at: datetime
    ) -> None: ...

    async def update_status(
        self, channel_id: str, thread_ts: str, status: ConversationStatus
    ) -> None: ...

    async def update_reminder_sent(
        self, channel_id: str, thread_ts: str, at: datetime
    ) -> None: ...


class KeywordRepositoryPort(Protocol):
    async def add(self, word: str) -> bool: ...

    async def remove(self, word: str) -> bool: ...

    async def list_all(self) -> list[str]: ...


class ChannelCursorPort(Protocol):
    async def get_cursor(self, integration_id: str, channel_id: str) -> str | None: ...

    async def upsert_cursor(self, integration_id: str, channel_id: str, ts: str) -> None: ...


class MessengerPort(Protocol):
    """Port for sending messages back into the messaging platform."""

    async def send_dm(self, user_id: str, text: str) -> None: ...

    async def send_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None: ...

    async def get_channel_name(self, channel_id: str) -> str: ...

    async def get_message_text(self, channel_id: str, ts: str) -> str: ...

    def build_thread_link(self, channel_id: str, thread_ts: str) -> str: ...
