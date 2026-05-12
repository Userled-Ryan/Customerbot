from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from customerbot.domain.tracking.entities import UserSettings
from customerbot.domain.tracking.ports import (
    ConversationRepositoryPort,
    MessengerPort,
    UserSettingsRepositoryPort,
)

logger = logging.getLogger(__name__)

_MORNING_HOUR = 9
_EVENING_HOUR = 17
_FIRE_WINDOW_SECONDS = 300  # fire within 5 minutes of the scheduled time


def _format_age(hours: float) -> str:
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(hours)}h"
    days = int(hours / 24)
    remaining = int(hours % 24)
    return f"{days}d {remaining}h" if remaining else f"{days}d"


def _get_tz(settings: UserSettings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r, falling back to UTC", settings.timezone)
        return ZoneInfo("UTC")


class SendDailyDigest:
    """Background use case: send scheduled morning and evening digests."""

    def __init__(
        self,
        repo: ConversationRepositoryPort,
        messenger: MessengerPort,
        user_settings_repo: UserSettingsRepositoryPort,
        ryan_user_id: str,
    ) -> None:
        self._repo = repo
        self._messenger = messenger
        self._user_settings_repo = user_settings_repo
        self._ryan_user_id = ryan_user_id

    async def execute(self) -> None:
        settings = await self._user_settings_repo.get(self._ryan_user_id)
        if settings is None:
            settings = UserSettings(user_id=self._ryan_user_id)

        if not settings.daily_digest_enabled:
            return

        tz = _get_tz(settings)
        now = datetime.now(tz)
        today = now.date().isoformat()

        morning = now.replace(hour=_MORNING_HOUR, minute=0, second=0, microsecond=0)
        if (
            now >= morning
            and (now - morning).total_seconds() < _FIRE_WINDOW_SECONDS
            and settings.last_morning_digest_date != today
        ):
            await self._send_morning_digest()
            settings.last_morning_digest_date = today
            await self._user_settings_repo.save(settings)

        evening = now.replace(hour=_EVENING_HOUR, minute=0, second=0, microsecond=0)
        if (
            now >= evening
            and (now - evening).total_seconds() < _FIRE_WINDOW_SECONDS
            and settings.last_evening_digest_date != today
        ):
            await self._send_evening_digest(settings, tz)
            settings.last_evening_digest_date = today
            await self._user_settings_repo.save(settings)

    async def _send_morning_digest(self) -> None:
        open_convs = await self._repo.find_open()
        if not open_convs:
            return

        lines = [f"🌅 *Good morning — {len(open_convs)} open ticket{'s' if len(open_convs) != 1 else ''}*\n"]
        for conv in open_convs:
            link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
            age = _format_age(conv.hours_since_last_reply())
            label = conv.channel_name or conv.channel_id
            ticket_id = f" `#{conv.ticket_number}`" if conv.ticket_number is not None else ""
            lines.append(f"•{ticket_id} <{link}|#{label}> · {conv.category.title()} · {age} old")

        ids = " ".join(str(c.ticket_number) for c in open_convs if c.ticket_number is not None)
        if ids:
            lines.append(f"\n_Close with `/csbot close {ids}`_")

        await self._messenger.send_dm(self._ryan_user_id, "\n".join(lines))

    async def _send_evening_digest(self, settings: UserSettings, tz: ZoneInfo) -> None:
        open_convs = await self._repo.find_open()
        if not open_convs:
            return

        today_start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_local.astimezone(timezone.utc).replace(tzinfo=None)

        new_today = []
        overdue = []
        for conv in open_convs:
            interval = conv.effective_reminder_hours(settings.default_reminder_hours)
            is_overdue = conv.is_overdue(interval)
            is_new = conv.opened_at >= today_start_utc
            if is_overdue:
                overdue.append(conv)
            elif is_new:
                new_today.append(conv)

        if not new_today and not overdue:
            return

        lines = ["🌆 *End-of-day digest*\n"]

        if new_today:
            lines.append(f"*🆕 New today ({len(new_today)})*")
            for conv in new_today:
                link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
                label = conv.channel_name or conv.channel_id
                ticket_id = f" `#{conv.ticket_number}`" if conv.ticket_number is not None else ""
                lines.append(f"•{ticket_id} <{link}|#{label}> · {conv.category.title()}")

        if overdue:
            if new_today:
                lines.append("")
            lines.append(f"*🔴 Overdue ({len(overdue)})*")
            for conv in overdue:
                link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
                age = _format_age(conv.hours_since_last_reply())
                label = conv.channel_name or conv.channel_id
                interval = conv.effective_reminder_hours(settings.default_reminder_hours)
                ticket_id = f" `#{conv.ticket_number}`" if conv.ticket_number is not None else ""
                lines.append(f"•{ticket_id} <{link}|#{label}> · {conv.category.title()} · {age} old (SLA: {interval}h)")

        all_tickets = new_today + overdue
        ids = " ".join(str(c.ticket_number) for c in all_tickets if c.ticket_number is not None)
        if ids:
            lines.append(f"\n_Close with `/csbot close {ids}`_")

        await self._messenger.send_dm(self._ryan_user_id, "\n".join(lines))

    async def run_loop(self, interval_seconds: int = 60) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Error in daily digest loop")
            await asyncio.sleep(interval_seconds)
