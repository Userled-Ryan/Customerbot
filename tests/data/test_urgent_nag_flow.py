"""Integration tests for the hourly urgent-ticket nag.

Covers:
- `UrgentNagJob` fires only near the top of the hour, at most once per hour.
- It DMs only *effectively urgent* tickets (urgent flag + still NEW) — moving a
  ticket to In progress / Resolved, or a non-urgent ticket, is skipped.
- Reminders are grouped per SE owner.
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


def _job(tickets: SQLiteTicketRepository, fake_slack: FakeSlackPort) -> UrgentNagJob:
    return UrgentNagJob(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
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
