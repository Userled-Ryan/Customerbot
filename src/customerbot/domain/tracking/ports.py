from __future__ import annotations

from typing import Protocol


class ChannelCursorPort(Protocol):
    async def get_cursor(self, integration_id: str, channel_id: str) -> str | None: ...

    async def upsert_cursor(self, integration_id: str, channel_id: str, ts: str) -> None: ...
