from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from customerbot.domain.tracking.entities import TrackedConversation
from customerbot.domain.tracking.ports import ConversationRepositoryPort, MessengerPort

logger = logging.getLogger(__name__)


def _format_hours(hours: float) -> str:
    if hours < 24:
        return f"{int(hours)}h"
    days = int(hours / 24)
    remaining = int(hours % 24)
    return f"{days}d {remaining}h" if remaining else f"{days}d"


class SendReminders:
    """Use case: find overdue conversations and DM Ryan a reminder."""

    def __init__(
        self,
        repo: ConversationRepositoryPort,
        messenger: MessengerPort,
        ryan_user_id: str,
        reminder_hours: int = 24,
    ) -> None:
        self._repo = repo
        self._messenger = messenger
        self._ryan_user_id = ryan_user_id
        self._reminder_hours = reminder_hours

    async def execute(self) -> None:
        overdue = await self._repo.find_overdue(self._reminder_hours)
        if not overdue:
            return

        # Only remind about conversations that haven't been reminded in the last window
        to_remind = [
            c for c in overdue
            if c.reminder_sent_at is None or c.is_overdue(self._reminder_hours)
            and (datetime.utcnow() - c.reminder_sent_at).total_seconds() / 3600 >= self._reminder_hours
        ]
        if not to_remind:
            return

        lines = [f"⏰ *{len(to_remind)} conversation{'s' if len(to_remind) > 1 else ''} need your attention*\n"]
        for conv in to_remind:
            link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
            age = _format_hours(conv.hours_since_last_reply())
            label = conv.channel_name or conv.channel_id
            lines.append(f"• <{link}|#{label}> · {conv.category.value.title()} · no reply for {age}")

        lines.append("\n_Reply in the thread, or close it with `/customerbot close`_")
        message = "\n".join(lines)

        await self._messenger.send_dm(self._ryan_user_id, message)

        now = datetime.utcnow()
        for conv in to_remind:
            await self._repo.update_reminder_sent(conv.channel_id, conv.thread_ts, now)

        logger.info("Sent reminder for %d overdue conversations", len(to_remind))

    async def run_loop(self, interval_seconds: int = 3600) -> None:
        """Background loop: check for overdue conversations every hour."""
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Error in reminder loop")
            await asyncio.sleep(interval_seconds)
