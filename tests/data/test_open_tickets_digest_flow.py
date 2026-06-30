"""Integration tests for the twice-daily open-tickets digest.

Covers:
- `OpenTicketsDigestJob` only DMs inside the 10:00 and 17:00 SE-local hours,
  at most once per window per local day, and stays quiet when nothing's open.
- The digest lists only tickets needing SE action (New + In progress) and
  excludes Awaiting customer.
- `render_open_tickets_blocks` carries counts-by-tier and a reply-needed marker.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.open_tickets_digest import (
    OpenTicketsDigestJob,
    render_open_tickets_blocks,
)
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort

# 2026-06-01 is a Monday; times chosen relative to the 10:00 / 17:00 windows.
_MON_1000_UTC = datetime(2026, 6, 1, 10, 0)
_MON_1030_UTC = datetime(2026, 6, 1, 10, 30)
_MON_1300_UTC = datetime(2026, 6, 1, 13, 0)
_MON_1700_UTC = datetime(2026, 6, 1, 17, 0)
_TUE_1000_UTC = datetime(2026, 6, 2, 10, 0)


def _bug(
    *,
    priority: Priority = Priority.P2,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    title: str = "checkout broken",
    reply_needed: bool = False,
    created_at: datetime = datetime(2026, 5, 30, 9, 0),
) -> Ticket:
    return Ticket(
        title=title,
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        priority=priority,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        reply_needed=reply_needed,
        created_at=created_at,
    )


def _job(tickets: SQLiteTicketRepository, fake_slack: FakeSlackPort) -> OpenTicketsDigestJob:
    return OpenTicketsDigestJob(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        se_timezone="UTC",
        workspace_url="https://test.slack.com",
    )


# --- Firing windows ---------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_does_not_fire_outside_windows(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_bug())
    fired = await _job(tickets, fake_slack).execute(now_utc=_MON_1300_UTC)
    assert fired is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_digest_fires_at_10_and_17_then_throttles_per_window(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_bug())
    job = _job(tickets, fake_slack)

    # 10:00 window fires once.
    assert await job.execute(now_utc=_MON_1000_UTC) is True
    assert [u for u, _, _ in fake_slack.dm_blocks_sent] == ["U_SE"]
    # Second tick inside the same 10:00 window — already fired, skip.
    assert await job.execute(now_utc=_MON_1030_UTC) is False
    assert len(fake_slack.dm_blocks_sent) == 1

    # 17:00 window is a separate fire the same day.
    assert await job.execute(now_utc=_MON_1700_UTC) is True
    assert len(fake_slack.dm_blocks_sent) == 2

    # Next day's 10:00 window fires again.
    assert await job.execute(now_utc=_TUE_1000_UTC) is True
    assert len(fake_slack.dm_blocks_sent) == 3


@pytest.mark.asyncio
async def test_digest_stays_quiet_when_nothing_open(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    # Only an awaiting-customer ticket — excluded from "needs action".
    await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    job = _job(tickets, fake_slack)
    assert await job.execute(now_utc=_MON_1000_UTC) is False
    assert fake_slack.dm_blocks_sent == []
    # Quiet run didn't burn the window — a ticket opened later still fires.
    await tickets.create(_bug(status=TicketStatus.NEW))
    assert await job.execute(now_utc=_MON_1030_UTC) is True


@pytest.mark.asyncio
async def test_digest_excludes_awaiting_customer(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_bug(status=TicketStatus.NEW, title="needs-me"))
    await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER, title="with-customer"))
    job = _job(tickets, fake_slack)
    assert await job.execute(now_utc=_MON_1000_UTC) is True
    _user, blocks, _text = fake_slack.dm_blocks_sent[0]
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    assert "needs-me" in rendered
    assert "with-customer" not in rendered


# --- Pure rendering ---------------------------------------------------------


def test_render_counts_by_tier_and_reply_marker() -> None:
    tickets = [
        _bug(priority=Priority.P1, title="urgent", reply_needed=True),
        _bug(priority=Priority.P2, title="medium"),
        _bug(priority=Priority.P2, title="medium2"),
    ]
    blocks = render_open_tickets_blocks(
        tickets, now=_MON_1000_UTC, workspace_url="https://test.slack.com"
    )
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    # Counts-by-tier header (folded in from the old weekly digest).
    assert "*P1* 1" in rendered
    assert "*P2* 2" in rendered
    # Reply-needed ticket carries the marker (folded in from the reply digest).
    assert ":speech_balloon:" in rendered
    # P1 sorts above P2 (higher priority first).
    assert rendered.index("urgent") < rendered.index("medium")
