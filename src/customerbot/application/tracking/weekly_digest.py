"""Weekly digest — Mondays 09:00 SE-local (flow §5d, plan Chunk 13).

A single message posted to `SE_TICKETS_CHANNEL_ID` once per week, every
Monday at 09:00 SE-local time. Contents per flow §5d:

- Counts of live tickets by priority tier
- SLA breach rate (RED in any clock stage over the last 7 days)
- Oldest still-open ticket per tier

Throttled via the `weekly_digest_state` singleton row's `last_fired_at`:
the job won't refire if we've already sent a digest in the current
ISO-week regardless of how often the loop ticks.

Replaces the legacy `send_daily_digest.py` (now retired). The legacy
job fired twice a day off `tracked_conversations`; this one's weekly
off the v1 ticket store.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from customerbot.domain.bot_state.entities import SLAStage, SLAState, WeeklyDigestState
from customerbot.domain.bot_state.ports import (
    SLADMStateRepositoryPort,
    WeeklyDigestStateRepositoryPort,
)
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import Priority, TicketStatus

logger = logging.getLogger(__name__)

FIRE_HOUR = 9  # 09:00 SE-local

_PRIO_ORDER: tuple[Priority, ...] = (
    Priority.P0,
    Priority.P1,
    Priority.P2,
    Priority.P3,
    Priority.P4,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown SE timezone %r — falling back to UTC for weekly digest", timezone_name
        )
        return ZoneInfo("UTC")


class WeeklyDigestJob:
    """Scheduled job — posts the digest once per ISO-week."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        sla_state: SLADMStateRepositoryPort,
        digest_state: WeeklyDigestStateRepositoryPort,
        slack: SlackPort,
        digest_channel_id: str | None,
        se_timezone: str,
    ) -> None:
        self._tickets = tickets
        self._sla_state = sla_state
        self._digest_state = digest_state
        self._slack = slack
        self._digest_channel_id = digest_channel_id
        self._tz_name = se_timezone

    async def execute(self, *, now_utc: datetime | None = None) -> bool:
        """Return True if a digest was posted this tick."""
        if self._digest_channel_id is None:
            logger.warning("SE_TICKETS_CHANNEL_ID not set — weekly digest can't fire")
            return False
        when = now_utc or _utcnow()
        tz = _tz(self._tz_name)
        local = when.replace(tzinfo=UTC).astimezone(tz)
        # Monday at 09:00 local, within the 09:00–10:00 hour so the daily loop tick has a window.
        if local.weekday() != 0 or local.hour < FIRE_HOUR or local.hour >= FIRE_HOUR + 1:
            return False

        state = await self._digest_state.get()
        if state.last_fired_at is not None and _same_iso_week(state.last_fired_at, when):
            return False

        live = await self._tickets.query_live()
        blocks = await self._build_digest_blocks(live, now=when)
        await self._slack.send_blocks(
            self._digest_channel_id,
            blocks,
            text=f":bar_chart: Weekly ticket digest — {local.strftime('%a %d %b %Y')}",
        )
        await self._digest_state.update(
            WeeklyDigestState(last_fired_at=when),
            now=when,
        )
        return True

    async def _build_digest_blocks(
        self, live: list[Ticket], *, now: datetime
    ) -> list[dict[str, Any]]:
        counts = _counts_by_tier(live)
        oldest = _oldest_per_tier(live, now=now)
        breach_rate = await self._compute_breach_rate(live)
        return render_digest_blocks(
            counts=counts,
            oldest=oldest,
            breach_rate=breach_rate,
            total_live=len(live),
            now=now,
        )

    async def _compute_breach_rate(self, live: list[Ticket]) -> float:
        """Fraction of live tickets currently in any RED SLA clock stage.

        We walk `sla_dm_state` rather than recomputing SLA clocks here — the
        Chunk 8 state machine writes the latest state per (ticket, stage), so
        a ticket with any RED row counts as breached for the digest.
        """
        if not live:
            return 0.0
        breached = 0
        for ticket in live:
            if ticket.id is None:
                continue
            for stage in _CLOCK_STAGES_FOR_DIGEST:
                record = await self._sla_state.get(ticket.id, stage)
                if record is not None and record.last_state == SLAState.RED:
                    breached += 1
                    break
        return breached / len(live)

    async def run_loop(self, interval_seconds: int = 1800) -> None:
        """30-minute loop — gives the Monday-09:00 window plenty of chances to fire."""
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Weekly-digest loop error")
            await asyncio.sleep(interval_seconds)


# Stages where a RED state genuinely means "breached SLA" (vs the awaiting-nudge
# stages, which use RED as a "nudge sent" marker).
_CLOCK_STAGES_FOR_DIGEST: tuple[SLAStage, ...] = (
    SLAStage.FIRST_RESPONSE,
    SLAStage.STATUS_UPDATE,
    SLAStage.RESOLUTION,
)


def _same_iso_week(a: datetime, b: datetime) -> bool:
    return a.isocalendar()[:2] == b.isocalendar()[:2]


def _counts_by_tier(tickets: list[Ticket]) -> dict[Priority, int]:
    counts: dict[Priority, int] = dict.fromkeys(_PRIO_ORDER, 0)
    for t in tickets:
        if t.status == TicketStatus.CLOSED:
            continue
        counts[t.priority] = counts.get(t.priority, 0) + 1
    return counts


def _oldest_per_tier(tickets: list[Ticket], *, now: datetime) -> dict[Priority, Ticket | None]:
    oldest: dict[Priority, Ticket | None] = dict.fromkeys(_PRIO_ORDER, None)
    for t in tickets:
        if t.status == TicketStatus.CLOSED:
            continue
        current = oldest.get(t.priority)
        if current is None or t.created_at < current.created_at:
            oldest[t.priority] = t
    _ = now  # reserved for future "age cutoff" filtering
    return oldest


def render_digest_blocks(
    *,
    counts: dict[Priority, int],
    oldest: dict[Priority, Ticket | None],
    breach_rate: float,
    total_live: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """Pure rendering — separated so tests can assert without running the job."""
    headline = (
        f":bar_chart: *Weekly digest* — *{total_live}* live ticket(s), "
        f"*{breach_rate:.0%}* in SLA breach"
    )
    if total_live == 0:
        headline = ":bar_chart: *Weekly digest* — _no live tickets._"

    counts_lines = [
        f"• *{tier.value}* — {counts.get(tier, 0)}"
        for tier in _PRIO_ORDER
        if counts.get(tier, 0) > 0
    ]
    counts_text = "\n".join(counts_lines) if counts_lines else "_no live tickets in any tier._"

    oldest_lines: list[str] = []
    for tier in _PRIO_ORDER:
        ticket = oldest.get(tier)
        if ticket is None:
            continue
        age = _age_phrase(now - ticket.created_at)
        oldest_lines.append(
            f"• *{tier.value}* — {ticket.display_id} _{_truncate(ticket.title, 60)}_ (opened {age})"
        )
    oldest_text = "\n".join(oldest_lines) if oldest_lines else "_n/a — no live tickets._"

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Counts by tier*\n{counts_text}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Oldest open per tier*\n{oldest_text}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"_Digest generated {now.strftime('%a %d %b %Y %H:%M UTC')}. "
                        f"First-week calibration item — flow §18 says SLA targets "
                        f"are revisable after 4 weeks of real data._"
                    ),
                }
            ],
        },
    ]


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
