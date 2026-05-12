from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from customerbot.domain.tracking.ports import (
    ConversationRepositoryPort,
    MessengerPort,
    UserSettingsRepositoryPort,
)

logger = logging.getLogger(__name__)


def _format_hours(hours: float) -> str:
    if hours < 24:
        return f"{int(hours)}h"
    days = int(hours / 24)
    remaining = int(hours % 24)
    return f"{days}d {remaining}h" if remaining else f"{days}d"


class SendReminders:
    """Use case: find overdue conversations and DM the user a reminder."""

    def __init__(
        self,
        repo: ConversationRepositoryPort,
        messenger: MessengerPort,
        user_settings_repo: UserSettingsRepositoryPort,
        ryan_user_id: str,
        reminder_hours: int = 24,
    ) -> None:
        self._repo = repo
        self._messenger = messenger
        self._user_settings_repo = user_settings_repo
        self._ryan_user_id = ryan_user_id
        self._default_reminder_hours = reminder_hours

    async def execute(self) -> None:
        settings = await self._user_settings_repo.get(self._ryan_user_id)
        user_default = settings.default_reminder_hours if settings else self._default_reminder_hours

        open_convs = await self._repo.find_open()
        now = datetime.utcnow()

        to_remind = []
        for conv in open_convs:
            interval = conv.effective_reminder_hours(user_default)
            if not conv.is_overdue(interval):
                continue
            if conv.reminder_sent_at is None:
                to_remind.append(conv)
            elif (now - conv.reminder_sent_at).total_seconds() / 3600 >= interval:
                to_remind.append(conv)

        if not to_remind:
            return

        lines = [f"⏰ *{len(to_remind)} conversation{'s' if len(to_remind) != 1 else ''} need your attention*\n"]
        for conv in to_remind:
            link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
            age = _format_hours(conv.hours_since_last_reply())
            label = conv.channel_name or conv.channel_id
            ticket_id = f" `#{conv.ticket_number}`" if conv.ticket_number is not None else ""
            interval = conv.effective_reminder_hours(user_default)
            lines.append(f"•{ticket_id} <{link}|#{label}> · {conv.category.title()} · no reply for {age} (reminder every {interval}h)")

        ids = " ".join(str(c.ticket_number) for c in to_remind if c.ticket_number is not None)
        close_hint = f"`/csbot close {ids}`" if ids else "`/csbot close <id>`"
        lines.append(f"\n_Reply in the thread, or close it here with {close_hint}_")

        await self._messenger.send_dm(self._ryan_user_id, "\n".join(lines))

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
