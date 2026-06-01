"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
    run_migrations,
)
from customerbot.domain.messaging.ports import ThreadMessage


@pytest_asyncio.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "test.db"
    url = database_url_from_path(str(db_path))
    run_migrations(url)
    engine = make_engine(url)
    factory = make_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


@dataclass
class FakeSlackPort:
    """In-memory recorder implementing `SlackPort`. Reused across pipeline tests."""

    workspace_url: str = "https://test.slack.com"
    next_view_id: str = "V_TEST"
    next_message_ts: str = "1700000000.000100"
    dms_sent: list[tuple[str, str]] = field(default_factory=list)
    messages_sent: list[tuple[str, str, str | None]] = field(default_factory=list)
    blocks_posted: list[tuple[str, list[dict[str, Any]], str]] = field(default_factory=list)
    messages_updated: list[tuple[str, str, list[dict[str, Any]], str]] = field(default_factory=list)
    views_opened: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    dm_blocks_sent: list[tuple[str, list[dict[str, Any]], str]] = field(default_factory=list)
    user_group_memberships: dict[str, set[str]] = field(default_factory=dict)
    thread_messages: dict[tuple[str, str], list[ThreadMessage]] = field(default_factory=dict)

    async def send_dm(self, user_id: str, text: str) -> None:
        self.dms_sent.append((user_id, text))

    async def send_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.messages_sent.append((channel_id, text, thread_ts))

    async def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> str | None:
        self.blocks_posted.append((channel_id, blocks, text))
        return self.next_message_ts

    async def update_message(
        self,
        channel_id: str,
        message_ts: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> None:
        self.messages_updated.append((channel_id, message_ts, blocks, text))

    async def open_view(self, trigger_id: str, view: dict[str, Any]) -> str | None:
        self.views_opened.append((trigger_id, view))
        return self.next_view_id

    async def get_channel_name(self, channel_id: str) -> str:
        return channel_id

    async def send_dm_blocks(
        self,
        user_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> tuple[str, str] | None:
        self.dm_blocks_sent.append((user_id, blocks, text))
        return (f"D_for_{user_id}", self.next_message_ts)

    async def is_user_in_group(self, user_id: str, group_id: str) -> bool:
        return user_id in self.user_group_memberships.get(group_id, set())

    async def get_thread_messages(
        self, channel_id: str, thread_ts: str, *, limit: int = 5
    ) -> list[ThreadMessage]:
        msgs = self.thread_messages.get((channel_id, thread_ts), [])
        return list(msgs)[-limit:]

    def build_thread_link(self, channel_id: str, thread_ts: str) -> str:
        clean = thread_ts.replace(".", "")
        return f"{self.workspace_url}/archives/{channel_id}/p{clean}"


@pytest.fixture
def fake_slack() -> FakeSlackPort:
    return FakeSlackPort()
