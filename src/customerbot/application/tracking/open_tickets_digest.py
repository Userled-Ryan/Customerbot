"""Open-tickets digest — DM'd to the SE twice a day, 10:00 and 17:00 SE-local.

This is the single ticket roll-up the SE asked for: instead of per-transition
SLA escalation pings landing throughout the day (now silenced — the SLA clocks
still tick for reporting, they just don't DM), the bot DMs *one* digest at 10am
and one at 5pm listing the tickets whose deadline is *due today or overdue*.

Scope = live tickets in `New` or `In progress` (the states needing SE action —
`Awaiting customer` and the terminal `Resolved` / `Closed` states are excluded)
whose `deadline` falls on or before today (SE-local). Tickets with no deadline,
or a deadline still in the future, are left out — the SE asked to keep this
alert to what's actually due, not the whole open backlog.

It folds in the two digests it replaces:
- the weekly digest's *counts-by-tier* header, and
- the reply-needed digest's flag — tickets the SE marked `Reply needed` get a
  `:speech_balloon:` marker so they stand out in the same list.

Firing mirrors the other digest jobs' "coarse loop + fire-window" shape: a
30-minute loop calls `execute()`, which acts only inside the 10:00–11:00 and
17:00–18:00 SE-local hours and at most once per window per local day. Throttling
is *persisted* (the `weekly_digest_state` singleton — leftover from the weekly
digest this replaced, reused here) rather than in-memory, so a process restart
inside a fire hour no longer resends the digest. A single stored timestamp
covers both windows: we only ever record a fire time, whose SE-local hour is
exactly the window it fired for, so comparing local date + hour tells the two
apart. When nothing is due the bot stays quiet (no empty digest) and doesn't
burn the window, so a ticket that gets a today deadline mid-window is still
picked up on the next tick.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from customerbot.application.tracking.links import linked_display_id
from customerbot.domain.bot_state.entities import WeeklyDigestState
from customerbot.domain.bot_state.ports import WeeklyDigestStateRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import Priority, TicketStatus

logger = logging.getLogger(__name__)

# The two SE-local hours we DM in. Each fires once per local day.
FIRE_HOURS: tuple[int, ...] = (10, 17)

# Tickets that "need the SE's action" — everything live except Awaiting customer.
_ACTION_STATUSES: frozenset[TicketStatus] = frozenset({TicketStatus.NEW, TicketStatus.IN_PROGRESS})

_PRIO_ORDER: tuple[Priority, ...] = (
    Priority.P0,
    Priority.P1,
    Priority.P2,
    Priority.P3,
    Priority.P4,
)

_STATUS_LABEL: dict[TicketStatus, str] = {
    TicketStatus.NEW: "New",
    TicketStatus.IN_PROGRESS: "In progress",
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _already_fired_window(
    last_fired_at: datetime | None, local: datetime, window: int, tz: ZoneInfo
) -> bool:
    """Did we already fire *this* window today?

    Persisting the last-fired time (rather than an in-memory dict) means a
    process restart inside a fire hour doesn't re-send the digest. One stored
    timestamp disambiguates both windows: we only ever store a fire time, whose
    SE-local hour is exactly the window it fired for (10 or 17), so matching on
    local date *and* hour tells the two windows apart.
    """
    if last_fired_at is None:
        return False
    lf_local = last_fired_at.replace(tzinfo=UTC).astimezone(tz)
    return lf_local.date() == local.date() and lf_local.hour == window


def _tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown SE timezone %r — falling back to UTC for open-tickets digest",
            timezone_name,
        )
        return ZoneInfo("UTC")


class OpenTicketsDigestJob:
    """Scheduled job — DMs the SE the open-tickets roll-up at 10:00 and 17:00."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        slack: SlackPort,
        state: WeeklyDigestStateRepositoryPort,
        se_user_id: str,
        se_timezone: str,
        workspace_url: str,
    ) -> None:
        self._tickets = tickets
        self._slack = slack
        self._state = state
        self._se_user_id = se_user_id
        self._tz_name = se_timezone
        self._workspace_url = workspace_url

    async def execute(self, *, now_utc: datetime | None = None) -> bool:
        """Return True if a digest DM was sent this tick."""
        when = now_utc or _utcnow()
        tz = _tz(self._tz_name)
        local = when.replace(tzinfo=UTC).astimezone(tz)
        window = next((h for h in FIRE_HOURS if local.hour == h), None)
        if window is None:
            return False

        state = await self._state.get()
        if _already_fired_window(state.last_fired_at, local, window, tz):
            return False

        today = local.date()
        live = await self._tickets.query_live()
        open_tickets = [
            t
            for t in live
            if t.status in _ACTION_STATUSES and t.deadline is not None and t.deadline <= today
        ]
        if not open_tickets:
            # Nothing due today — stay quiet and don't burn the window, so a
            # ticket that gets a today deadline later this hour still fires.
            return False

        blocks = render_open_tickets_blocks(
            open_tickets, now=when, today=today, workspace_url=self._workspace_url
        )
        await self._slack.send_dm_blocks(
            self._se_user_id,
            blocks,
            text=f":alarm_clock: {len(open_tickets)} ticket(s) due today",
        )
        await self._state.update(WeeklyDigestState(last_fired_at=when), now=when)
        return True

    async def run_loop(self, interval_seconds: int = 1800) -> None:
        """30-minute loop — gives each fire hour plenty of chances to fire."""
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Open-tickets-digest loop error")
            await asyncio.sleep(interval_seconds)


def render_open_tickets_blocks(
    tickets: list[Ticket], *, now: datetime, today: date, workspace_url: str
) -> list[dict[str, Any]]:
    """Pure rendering — separated so tests can assert without running the job."""
    counts = _counts_by_tier(tickets)
    counts_line = " · ".join(
        f"*{tier.value}* {counts[tier]}" for tier in _PRIO_ORDER if counts[tier] > 0
    )
    headline = f":alarm_clock: *Due today* — *{len(tickets)}* ticket(s)\n{counts_line}"

    # Priority first (P0 → P4), then oldest first within a tier.
    ordered = sorted(tickets, key=lambda t: (_PRIO_ORDER.index(t.priority), t.created_at))
    lines: list[str] = []
    for t in ordered:
        label = linked_display_id(t, workspace_url)
        status = _STATUS_LABEL.get(t.status, t.status.value)
        age = _age_phrase(now - t.created_at)
        flag = " :speech_balloon:" if t.reply_needed else ""
        # Everything here is due today or overdue; flag the overdue ones so a
        # missed deadline stands out from one landing today.
        overdue_flag = ""
        if t.deadline is not None and t.deadline < today:
            overdue_flag = f" :rotating_light: overdue {(today - t.deadline).days}d"
        lines.append(
            f"• {label} — _{_truncate(t.title, 70)}_ "
            f"({t.priority.value} · {status} · opened {age}){overdue_flag}{flag}"
        )

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]
    if any(t.reply_needed for t in tickets):
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": ":speech_balloon: = you flagged this one Reply needed.",
                    }
                ],
            }
        )
    return blocks


def _counts_by_tier(tickets: list[Ticket]) -> dict[Priority, int]:
    counts: dict[Priority, int] = dict.fromkeys(_PRIO_ORDER, 0)
    for t in tickets:
        counts[t.priority] = counts.get(t.priority, 0) + 1
    return counts


def _age_phrase(delta: timedelta) -> str:
    days = delta.days
    if days >= 1:
        return f"{days}d ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h ago"
    return "just now"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
