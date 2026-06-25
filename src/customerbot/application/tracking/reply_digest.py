"""Daily reply-needed digest — 17:00 SE-local.

One DM to the SE each afternoon listing every live ticket they've flagged
`reply_needed` that's still outstanding. It's the failover behind the manual
flag: the SE sets "Reply needed" on a card, and if it's still set by 5pm they
get a single roll-up rather than per-ticket pings.

Mirrors `WeeklyDigestJob`'s "coarse loop + fire-window" shape: a 30-minute loop
calls `execute()`, which only acts inside the 17:00–18:00 SE-local hour and at
most once per local day. Throttling is in-memory (`_last_fired_date`) rather
than persisted — a process restart inside the 5pm hour could resend once, which
is an acceptable trade for not adding a state table. Harden with a persisted
bookmark if that ever bites.

Tickets with no `original_slack_link` still appear (so nothing flagged is
silently dropped); they just list without a thread link.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from customerbot.application.tracking.links import linked_display_id
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import TicketStatus

logger = logging.getLogger(__name__)

FIRE_HOUR = 17  # 17:00 SE-local


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown SE timezone %r — falling back to UTC for reply-needed digest",
            timezone_name,
        )
        return ZoneInfo("UTC")


class ReplyNeededDigestJob:
    """Scheduled job — DMs the SE the reply-needed roll-up once per day at 5pm."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        se_timezone: str,
        workspace_url: str,
    ) -> None:
        self._tickets = tickets
        self._slack = slack
        self._se_user_id = se_user_id
        self._tz_name = se_timezone
        self._workspace_url = workspace_url
        self._last_fired_date: date | None = None

    async def execute(self, *, now_utc: datetime | None = None) -> bool:
        """Return True if a digest DM was sent this tick."""
        when = now_utc or _utcnow()
        tz = _tz(self._tz_name)
        local = when.replace(tzinfo=UTC).astimezone(tz)
        # Only inside the 17:00–18:00 local hour, and at most once per local day.
        if local.hour < FIRE_HOUR or local.hour >= FIRE_HOUR + 1:
            return False
        if self._last_fired_date == local.date():
            return False

        live = await self._tickets.query_live()
        flagged = [t for t in live if t.reply_needed and t.status != TicketStatus.CLOSED]
        if not flagged:
            # Nothing outstanding — stay quiet (no empty digest), and don't burn
            # the day's single fire so a later-afternoon flag still gets picked up.
            return False

        blocks = render_reply_digest_blocks(flagged, workspace_url=self._workspace_url)
        await self._slack.send_dm_blocks(
            self._se_user_id,
            blocks,
            text=f":bell: {len(flagged)} ticket(s) still need a reply",
        )
        self._last_fired_date = local.date()
        return True

    async def run_loop(self, interval_seconds: int = 1800) -> None:
        """30-minute loop — gives the 17:00 window plenty of chances to fire."""
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Reply-needed-digest loop error")
            await asyncio.sleep(interval_seconds)


def render_reply_digest_blocks(
    tickets: list[Ticket], *, workspace_url: str
) -> list[dict[str, Any]]:
    """Pure rendering — separated so tests can assert without running the job."""
    headline = (
        f":bell: *Reply needed* — *{len(tickets)}* ticket(s) still waiting on a reply.\n"
        "_Reply in the thread, then clear the flag on the card._"
    )
    lines: list[str] = []
    for t in tickets:
        label = linked_display_id(t, workspace_url)
        lines.append(f"• {label} — _{_truncate(t.title, 70)}_ ({t.priority.value})")

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
