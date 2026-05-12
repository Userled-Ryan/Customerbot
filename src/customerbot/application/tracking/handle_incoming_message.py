from __future__ import annotations

import logging
from datetime import UTC, datetime

from customerbot.domain.tracking.entities import TrackedConversation
from customerbot.domain.tracking.ports import (
    ConversationRepositoryPort,
    KeywordRepositoryPort,
    MessengerPort,
)
from customerbot.domain.tracking.value_objects import ConversationStatus

logger = logging.getLogger(__name__)


class HandleIncomingMessage:
    """Use case: process a Slack message event and update conversation tracking.

    A new ticket is opened only when Ryan sends a message containing one of the
    configured keywords. Customer mentions of Ryan no longer create tickets —
    Slack already notifies him directly.
    """

    def __init__(
        self,
        repo: ConversationRepositoryPort,
        keywords: KeywordRepositoryPort,
        messenger: MessengerPort,
        ryan_user_id: str,
    ) -> None:
        self._repo = repo
        self._keywords = keywords
        self._messenger = messenger
        self._ryan_user_id = ryan_user_id

    async def execute(
        self,
        channel_id: str,
        thread_ts: str,
        sender_user_id: str,
        text: str,
    ) -> None:
        ryan_is_sender = sender_user_id == self._ryan_user_id
        if not ryan_is_sender:
            return

        now = datetime.now(UTC).replace(tzinfo=None)
        existing = await self._repo.find_by_thread(channel_id, thread_ts)

        if existing is None:
            keywords = await self._keywords.list_all()
            matched = _match_keyword(text, keywords) if keywords else None
            if matched is None:
                return
            channel_name = await self._messenger.get_channel_name(channel_id)
            context = text[:200].strip()
            conversation = TrackedConversation(
                channel_id=channel_id,
                thread_ts=thread_ts,
                channel_name=channel_name,
                category=matched,
                context=context,
                opened_at=now,
                last_ryan_reply_at=now,
            )
            await self._repo.upsert(conversation)
            logger.info("Opened conversation %s:%s", channel_id, thread_ts)
        elif existing.status == ConversationStatus.OPEN:
            await self._repo.update_last_reply(channel_id, thread_ts, now)
            logger.info("Updated last reply for %s:%s", channel_id, thread_ts)


def _match_keyword(text: str, keywords: list[tuple[str, str | None]]) -> str | None:
    """Return the category for the first matching keyword, or None if no match.

    Falls back to the keyword itself when the keyword has no explicit category.
    """
    haystack = text.lower()
    for word, category in keywords:
        if word in haystack:
            return category or word
    return None
