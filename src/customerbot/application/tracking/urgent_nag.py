"""Urgent-ticket nag — DMs the SE owner every hour on the hour, until actioned.

Urgent tickets (logged via the intake "Urgent" checkbox) have no deadline, so
they don't surface in the deadline-driven open-tickets digest. Instead this job
keeps them from slipping: while a ticket is *effectively urgent* (`is_urgent` —
flagged urgent **and** still `New`), its SE owner gets an hourly reminder. The
moment the SE moves it to In progress or Resolved it stops being urgent and the
nag ends — no explicit clearing, the `is_urgent` guard does it.

Firing shape mirrors the other scheduled jobs' "coarse loop + fire-window": a
60-second loop calls `execute()`, which fires only in the first few minutes of
each clock hour and at most once per hour (tracked in-memory, keyed on
`(date, hour)`). "On the hour" precision comes from the tight fire window, not
the loop cadence. A restart mid-hour can at worst re-fire once for that hour —
acceptable for a reminder, and no worse than the in-memory P0 scan dedupe.

Reminders are grouped per SE owner: one DM listing all of that owner's urgent
tickets, rather than N separate pings. Today every urgent ticket is assigned to
the configured SE, but grouping keeps it correct if that changes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from customerbot.application.tracking.links import linked_display_id
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort

logger = logging.getLogger(__name__)

# Fire only inside the first few minutes of the hour, so the reminder lands
# "on the hour" regardless of when in the hour the loop happens to tick.
FIRE_WINDOW_MINUTES = 5


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UrgentNagJob:
    """Scheduled job — hourly DM reminder for still-unactioned urgent tickets."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        workspace_url: str,
    ) -> None:
        self._tickets = tickets
        self._slack = slack
        self._se_user_id = se_user_id
        self._workspace_url = workspace_url
        # (date, hour) we last fired for — in-memory, so a restart may re-fire
        # once within the current hour. Fine for a reminder.
        self._last_fired: tuple[Any, int] | None = None

    async def execute(self, *, now_utc: datetime | None = None) -> bool:
        """Return True if at least one reminder DM was sent this tick."""
        when = now_utc or _utcnow()
        if when.minute >= FIRE_WINDOW_MINUTES:
            return False
        key = (when.date(), when.hour)
        if key == self._last_fired:
            return False

        live = await self._tickets.query_live()
        urgent = [t for t in live if t.is_urgent]
        if not urgent:
            # Nothing to nag — don't burn the hour, so a ticket logged later this
            # hour still gets picked up next hour (its create-time DM covers now).
            return False

        by_owner: dict[str, list[Ticket]] = defaultdict(list)
        for t in urgent:
            by_owner[t.se_owner_user_id or self._se_user_id].append(t)

        for owner, tickets in by_owner.items():
            blocks = render_urgent_nag_blocks(tickets, now=when, workspace_url=self._workspace_url)
            text = f":rotating_light: {len(tickets)} urgent ticket(s) awaiting action"
            try:
                await self._slack.send_dm_blocks(owner, blocks, text=text)
            except Exception:
                logger.exception("Urgent nag DM failed for owner %s", owner)

        self._last_fired = key
        return True

    async def run_loop(self, interval_seconds: int = 60) -> None:
        """60-second loop — guarantees a tick inside every hour's fire window."""
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Urgent-nag loop error")
            await asyncio.sleep(interval_seconds)


def render_urgent_nag_blocks(
    tickets: list[Ticket], *, now: datetime, workspace_url: str
) -> list[dict[str, Any]]:
    """Pure rendering — separated so tests can assert without running the job."""
    ordered = sorted(tickets, key=lambda t: t.created_at)
    headline = (
        f":rotating_light: *Urgent — {len(ordered)} ticket(s) still awaiting action*\n"
        "Move each to *In progress* or *Resolved* to stop these reminders."
    )
    lines: list[str] = []
    for t in ordered:
        label = linked_display_id(t, workspace_url)
        age = _age_phrase(now - t.created_at)
        lines.append(f"• {label} — _{_truncate(t.title, 70)}_ (opened {age})")
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]


def _age_phrase(delta: timedelta) -> str:
    days = delta.days
    if days >= 1:
        return f"{days}d ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h ago"
    minutes = delta.seconds // 60
    return f"{minutes}m ago" if minutes >= 1 else "just now"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
