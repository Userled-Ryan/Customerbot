"""Friday per-CSM digest — Fridays 12:00 SE-local.

Each CSM gets a single DM listing every live ticket touching one of their
customers (orgs they own), whoever raised it and whatever the type. It's the
weekly "here's everything in flight for your accounts" roll-up that complements
the SE-facing weekly digest.

Mirrors `WeeklyDigestJob`'s "coarse loop + fire-window + persisted bookmark"
shape: a 30-minute loop calls `execute()`, which only acts inside the Friday
12:00–13:00 SE-local hour and at most once per ISO-week. The bookmark is
persisted (`csm_digest_state`) — unlike the reply-needed digest's in-memory
throttle — because a restart inside the window would otherwise re-DM every CSM.

A CSM with no open tickets for their orgs is simply skipped (no empty DM).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from customerbot.application.tracking.csm_tickets import (
    CSMTicketsView,
    render_csm_tickets_blocks,
)
from customerbot.domain.bot_state.entities import CSMDigestState
from customerbot.domain.bot_state.ports import CSMDigestStateRepositoryPort
from customerbot.domain.messaging.ports import SlackPort

logger = logging.getLogger(__name__)

FIRE_WEEKDAY = 4  # Friday (Monday == 0)
FIRE_HOUR = 12  # 12:00 SE-local


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown SE timezone %r — falling back to UTC for Friday CSM digest",
            timezone_name,
        )
        return ZoneInfo("UTC")


def _same_iso_week(a: datetime, b: datetime) -> bool:
    return a.isocalendar()[:2] == b.isocalendar()[:2]


class FridayCSMDigestJob:
    """Scheduled job — DMs each CSM their customers' live tickets once per week."""

    def __init__(
        self,
        view: CSMTicketsView,
        digest_state: CSMDigestStateRepositoryPort,
        slack: SlackPort,
        se_timezone: str,
    ) -> None:
        self._view = view
        self._digest_state = digest_state
        self._slack = slack
        self._tz_name = se_timezone

    async def execute(self, *, now_utc: datetime | None = None) -> int:
        """Return the number of CSMs DMed this tick (0 if outside the window)."""
        when = now_utc or _utcnow()
        tz = _tz(self._tz_name)
        local = when.replace(tzinfo=UTC).astimezone(tz)
        # Friday at 12:00 local, within the 12:00–13:00 hour so the loop tick has a window.
        if local.weekday() != FIRE_WEEKDAY or local.hour < FIRE_HOUR or local.hour >= FIRE_HOUR + 1:
            return 0

        state = await self._digest_state.get()
        if state.last_fired_at is not None and _same_iso_week(state.last_fired_at, when):
            return 0

        grouped = await self._view.tickets_by_csm()
        sent = 0
        for csm_user_id, items in grouped.items():
            if not items:
                continue
            blocks = render_csm_tickets_blocks(
                items, workspace_url=self._view.workspace_url, scheduled=True
            )
            await self._slack.send_dm_blocks(
                csm_user_id,
                blocks,
                text=f":ticket: {len(items)} open ticket(s) for your customers this week",
            )
            sent += 1

        # Burn the week's fire even if no CSM had tickets — we still ran for the
        # window, and re-scanning every 30 min for a no-op helps nobody.
        await self._digest_state.update(CSMDigestState(last_fired_at=when), now=when)
        return sent

    async def run_loop(self, interval_seconds: int = 1800) -> None:
        """30-minute loop — gives the Friday-12:00 window plenty of chances to fire."""
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Friday-CSM-digest loop error")
            await asyncio.sleep(interval_seconds)
