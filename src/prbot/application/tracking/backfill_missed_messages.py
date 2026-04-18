from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from prbot.application.tracking.handle_incoming_message import HandleIncomingMessage
from prbot.domain.tracking.ports import ChannelCursorPort
from prbot.domain.tracking.value_objects import MessageRef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelDescriptor:
    """Integration-agnostic description of a channel to backfill."""

    channel_id: str
    team_id: str


@dataclass(frozen=True)
class HistoryItem:
    """A single message from channel history."""

    text: str
    ts: str
    channel_id: str
    team_id: str


FetchHistoryFn = Callable[
    [ChannelDescriptor, str],
    AsyncIterator[HistoryItem],
]
BuildMessageRefFn = Callable[[str, str], MessageRef]
BuildScopeKeysFn = Callable[[str, str], list[str]]


class BackfillMissedMessages:
    """Use case: on startup, scan channel history for messages missed during downtime."""

    def __init__(
        self,
        integration_id: str,
        cursor_repo: ChannelCursorPort,
        handle_incoming_message: HandleIncomingMessage,
        build_message_ref: BuildMessageRefFn,
        build_scope_keys: BuildScopeKeysFn,
    ) -> None:
        self._integration_id = integration_id
        self._cursor_repo = cursor_repo
        self._handle_incoming_message = handle_incoming_message
        self._build_message_ref = build_message_ref
        self._build_scope_keys = build_scope_keys

    async def execute(
        self,
        channels: list[ChannelDescriptor],
        fetch_history: FetchHistoryFn,
    ) -> None:
        if not channels:
            logger.info("Backfill: no channels to scan")
            return

        logger.info("Backfill: scanning %d channels", len(channels))
        total_processed = 0

        for channel in channels:
            cursor = await self._cursor_repo.get_cursor(self._integration_id, channel.channel_id)

            if cursor is None:
                # First boot for this channel — seed with current time, skip backfill
                now_ts = f"{time.time():.6f}"
                await self._cursor_repo.upsert_cursor(
                    self._integration_id, channel.channel_id, now_ts
                )
                logger.info(
                    "Backfill: seeded cursor for %s (no prior cursor)",
                    channel.channel_id,
                )
                continue

            logger.info("Backfill: checking %s since %s", channel.channel_id, cursor)

            latest_ts = cursor
            count = 0

            async for item in fetch_history(channel, cursor):
                message_ref = self._build_message_ref(item.channel_id, item.ts)
                scope_keys = self._build_scope_keys(item.team_id, item.channel_id)

                try:
                    await self._handle_incoming_message.execute(
                        message_ref=message_ref,
                        text=item.text,
                        scope_keys=scope_keys,
                    )
                except Exception:
                    logger.warning(
                        "Backfill: failed to process message %s in %s",
                        item.ts,
                        item.channel_id,
                        exc_info=True,
                    )

                if item.ts > latest_ts:
                    latest_ts = item.ts
                count += 1

            # Advance cursor: to latest message if any, otherwise to now
            advance_ts = latest_ts if latest_ts > cursor else f"{time.time():.6f}"
            await self._cursor_repo.upsert_cursor(
                self._integration_id, channel.channel_id, advance_ts
            )

            if count > 0:
                logger.info(
                    "Backfill: processed %d messages in %s",
                    count,
                    channel.channel_id,
                )
            total_processed += count

        logger.info("Backfill complete: %d messages processed", total_processed)
