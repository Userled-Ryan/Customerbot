from __future__ import annotations

import logging
from datetime import datetime

from prbot.domain.tracking.entities import TrackedConversation
from prbot.domain.tracking.ports import ConversationRepositoryPort, MessengerPort
from prbot.domain.tracking.value_objects import ConversationStatus

logger = logging.getLogger(__name__)


class HandleIncomingMessage:
    """Use case: process a Slack message event and update conversation tracking."""

    def __init__(
        self,
        repo: ConversationRepositoryPort,
        messenger: MessengerPort,
        ryan_user_id: str,
    ) -> None:
        self._repo = repo
        self._messenger = messenger
        self._ryan_user_id = ryan_user_id

    async def execute(
        self,
        channel_id: str,
        thread_ts: str,
        sender_user_id: str,
        text: str,
    ) -> None:
        ryan_mentioned = f"<@{self._ryan_user_id}>" in text
        ryan_is_sender = sender_user_id == self._ryan_user_id

        if not ryan_mentioned and not ryan_is_sender:
            return

        now = datetime.utcnow()
        existing = await self._repo.find_by_thread(channel_id, thread_ts)

        if existing is None:
            channel_name = await self._messenger.get_channel_name(channel_id)
            context = text[:200].strip()
            conversation = TrackedConversation(
                channel_id=channel_id,
                thread_ts=thread_ts,
                channel_name=channel_name,
                context=context,
                opened_at=now,
                last_ryan_reply_at=now if ryan_is_sender else None,
            )
            await self._repo.upsert(conversation)
            logger.info("Opened conversation %s:%s", channel_id, thread_ts)
        elif ryan_is_sender and existing.status == ConversationStatus.OPEN:
            await self._repo.update_last_reply(channel_id, thread_ts, now)
            logger.info("Updated last reply for %s:%s", channel_id, thread_ts)
