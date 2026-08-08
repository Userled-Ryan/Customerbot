"""Integration tests for the hourly urgent-ticket nag.

Covers:
- `UrgentNagJob` fires only near the top of the hour, at most once per hour.
- It DMs only *effectively urgent* tickets (urgent flag + still NEW) — moving a
  ticket to In progress / Resolved, or a non-urgent ticket, is skipped.
- Reminders are grouped per SE owner.
- Quiet hours: only inside the SE-local hour window, never at weekends.

All the `datetime`s here are naive UTC (what the job sees). 2026-06-01 is a
Monday, so the shared fixtures sit inside the default 07:00–23:00 window.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.urgent_nag import UrgentNagJob
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

_ON_THE_HOUR = datetime(2026, 6, 1, 14, 0)
_MID_HOUR = datetime(2026, 6, 1, 14, 30)
_NEXT_HOUR = datetime(2026, 6, 1, 15, 0)


def _urgent(
    *,
    status: TicketStatus = TicketStatus.NEW,
    urgent: bool = True,
    se_owner: str | None = "U_SE",
    title: str = "everything on fire",
) -> Ticket:
    return Ticket(
        title=title,
        type=TicketType.BUG,
        subtype=TicketSubtype.CUSTOMER_SPECIFIC,
        status=status,
        priority=Priority.P1,
        reporter_user_id="U_SE",
        se_owner_user_id=se_owner,
        source=Source.CUSTOMER_CHANNEL,
        urgent=urgent,
        created_at=datetime(2026, 5, 31, 9, 0),
    )


def _job(
    tickets: SQLiteTicketRepository,
    fake_slack: FakeSlackPort,
    *,
    se_timezone: str = "UTC",
    start_hour: int = 7,
    end_hour: int = 23,
) -> UrgentNagJob:
    return UrgentNagJob(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        se_timezone=se_timezone,
        start_hour=start_hour,
        end_hour=end_hour,
        workspace_url="https://test.slack.com",
    )


@pytest.mark.asyncio
async def test_nag_fires_on_the_hour(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    sent = await _job(tickets, fake_slack).execute(now_utc=_ON_THE_HOUR)
    assert sent is True
    assert [u for u, _b, _t in fake_slack.dm_blocks_sent] == ["U_SE"]


@pytest.mark.asyncio
async def test_nag_does_not_fire_mid_hour(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    sent = await _job(tickets, fake_slack).execute(now_utc=_MID_HOUR)
    assert sent is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_nag_fires_once_per_hour_then_again_next_hour(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    job = _job(tickets, fake_slack)
    assert await job.execute(now_utc=_ON_THE_HOUR) is True
    # A second tick in the same hour's window is deduped.
    assert await job.execute(now_utc=datetime(2026, 6, 1, 14, 1)) is False
    # The next clock hour fires again.
    assert await job.execute(now_utc=_NEXT_HOUR) is True
    assert len(fake_slack.dm_blocks_sent) == 2


@pytest.mark.asyncio
async def test_nag_skips_in_progress_and_non_urgent(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    # Urgent but already picked up → no longer effectively urgent.
    await tickets.create(_urgent(status=TicketStatus.IN_PROGRESS))
    # A normal (non-urgent) live ticket.
    await tickets.create(_urgent(urgent=False))
    sent = await _job(tickets, fake_slack).execute(now_utc=_ON_THE_HOUR)
    assert sent is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_nag_groups_by_owner(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent(se_owner="U_SE", title="a"))
    await tickets.create(_urgent(se_owner="U_SE", title="b"))
    await tickets.create(_urgent(se_owner="U_OTHER", title="c"))
    sent = await _job(tickets, fake_slack).execute(now_utc=_ON_THE_HOUR)
    assert sent is True
    recipients = sorted(u for u, _b, _t in fake_slack.dm_blocks_sent)
    # One grouped DM per owner (not one per ticket).
    assert recipients == ["U_OTHER", "U_SE"]


@pytest.mark.asyncio
async def test_nag_falls_back_to_se_when_owner_unset(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent(se_owner=None))
    await _job(tickets, fake_slack).execute(now_utc=_ON_THE_HOUR)
    assert [u for u, _b, _t in fake_slack.dm_blocks_sent] == ["U_SE"]


# --- quiet hours + weekends ---


@pytest.mark.asyncio
@pytest.mark.parametrize("hour", [7, 23])
async def test_nag_fires_at_the_window_bounds(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
    hour: int,
) -> None:
    """The 07:00–23:00 window is inclusive at both ends."""
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    sent = await _job(tickets, fake_slack).execute(now_utc=datetime(2026, 6, 1, hour, 0))
    assert sent is True
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("hour", [0, 3, 6])
async def test_nag_silent_overnight(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
    hour: int,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    sent = await _job(tickets, fake_slack).execute(now_utc=datetime(2026, 6, 1, hour, 0))
    assert sent is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("day", [6, 7], ids=["saturday", "sunday"])
async def test_nag_silent_at_weekends(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
    day: int,
) -> None:
    """2026-06-06 is a Saturday, 06-07 a Sunday — silent even mid-window."""
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    sent = await _job(tickets, fake_slack).execute(now_utc=datetime(2026, 6, day, 14, 0))
    assert sent is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_nag_window_is_se_local_not_utc(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """In June, Europe/London is BST (UTC+1) — the window shifts with it."""
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    job = _job(tickets, fake_slack, se_timezone="Europe/London")
    # 23:00 UTC Monday is 00:00 BST Tuesday — inside the UTC window, but quiet locally.
    assert await job.execute(now_utc=datetime(2026, 6, 1, 23, 0)) is False
    # 06:00 UTC is 07:00 BST — outside the UTC window, but the local window has opened.
    assert await job.execute(now_utc=datetime(2026, 6, 1, 6, 0)) is True
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
async def test_nag_unknown_timezone_falls_back_to_utc(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_urgent())
    job = _job(tickets, fake_slack, se_timezone="Mars/Phobos")
    assert await job.execute(now_utc=_ON_THE_HOUR) is True
