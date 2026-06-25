"""Integration tests for Chunk 13 — weekly digest + on-demand board.

Covers:
- `WeeklyDigestJob.execute` only fires on Mondays in the 09:00 SE-local
  hour, throttled once per ISO-week via `weekly_digest_state`.
- Digest counts open tickets by tier and computes breach rate from
  `sla_dm_state` rows in RED state.
- `render_digest_blocks` produces stable Block-Kit output (counts,
  oldest-per-tier, breach-rate).
- `RenderTicketsBoard` groups live tickets by lane × status with
  priority-ordered lines; empty case renders a single block.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.render_board import RenderTicketsBoard
from customerbot.application.tracking.weekly_digest import (
    WeeklyDigestJob,
    render_digest_blocks,
)
from customerbot.data.repository.bot_state import (
    SQLiteSLADMStateRepository,
    SQLiteWeeklyDigestStateRepository,
)
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort

# 2026-06-01 is a Monday — the digest's natural firing day.
_MONDAY_0900_UTC = datetime(2026, 6, 1, 9, 0)
_MONDAY_2300_UTC = datetime(2026, 6, 1, 23, 0)
_TUESDAY_0900_UTC = datetime(2026, 6, 2, 9, 0)
_NEXT_MONDAY_0900_UTC = datetime(2026, 6, 8, 9, 0)


def _bug(
    *,
    priority: Priority = Priority.P2,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    lane: Lane | None = Lane.SE_ACTION,
    title: str = "checkout broken",
    created_at: datetime = datetime(2026, 5, 25, 9, 0),
) -> Ticket:
    return Ticket(
        title=title,
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        lane=lane,
        priority=priority,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="users hang on submit",
        created_at=created_at,
    )


# --- Pure rendering ---------------------------------------------------------


def test_render_digest_blocks_counts_only_nonzero_tiers() -> None:
    counts: dict[Priority, int] = {
        Priority.P0: 0,
        Priority.P1: 2,
        Priority.P2: 5,
        Priority.P3: 0,
        Priority.P4: 1,
    }
    oldest: dict[Priority, Ticket | None] = dict.fromkeys(counts, None)
    blocks = render_digest_blocks(
        counts=counts,
        oldest=oldest,
        breach_rate=0.25,
        total_live=8,
        now=_MONDAY_0900_UTC,
    )
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    assert "8" in rendered  # total
    assert "25%" in rendered  # breach rate
    assert "*P1*" in rendered
    assert "*P2*" in rendered
    assert "*P4*" in rendered
    # P0 and P3 had zero — should be omitted.
    assert "*P0*" not in rendered
    assert "*P3*" not in rendered


def test_render_digest_blocks_empty_case_omits_breach_percentage() -> None:
    counts: dict[Priority, int] = dict.fromkeys(
        (Priority.P0, Priority.P1, Priority.P2, Priority.P3, Priority.P4),
        0,
    )
    oldest: dict[Priority, Ticket | None] = dict.fromkeys(counts, None)
    blocks = render_digest_blocks(
        counts=counts, oldest=oldest, breach_rate=0.0, total_live=0, now=_MONDAY_0900_UTC
    )
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    assert "no live tickets" in rendered


# --- WeeklyDigestJob --------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_does_not_fire_on_non_monday(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    digest = SQLiteWeeklyDigestStateRepository(session_factory)
    await tickets.create(_bug())
    job = WeeklyDigestJob(
        tickets=tickets,
        sla_state=sla,
        digest_state=digest,
        slack=fake_slack,
        digest_channel_id="C_SE_TICKETS",
        se_timezone="UTC",
    )
    fired = await job.execute(now_utc=_TUESDAY_0900_UTC)
    assert fired is False
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_digest_does_not_fire_outside_fire_hour(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    digest = SQLiteWeeklyDigestStateRepository(session_factory)
    await tickets.create(_bug())
    job = WeeklyDigestJob(
        tickets=tickets,
        sla_state=sla,
        digest_state=digest,
        slack=fake_slack,
        digest_channel_id="C_SE_TICKETS",
        se_timezone="UTC",
    )
    fired = await job.execute(now_utc=_MONDAY_2300_UTC)
    assert fired is False
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_digest_fires_on_monday_morning_then_throttles(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    digest = SQLiteWeeklyDigestStateRepository(session_factory)
    await tickets.create(_bug())
    job = WeeklyDigestJob(
        tickets=tickets,
        sla_state=sla,
        digest_state=digest,
        slack=fake_slack,
        digest_channel_id="C_SE_TICKETS",
        se_timezone="UTC",
    )
    first = await job.execute(now_utc=_MONDAY_0900_UTC)
    assert first is True
    assert len(fake_slack.blocks_posted) == 1
    channel, _blocks, _text = fake_slack.blocks_posted[0]
    assert channel == "C_SE_TICKETS"

    # Second tick same Monday hour — already fired this week, skip.
    fake_slack.blocks_posted.clear()
    second = await job.execute(now_utc=_MONDAY_0900_UTC.replace(minute=30))
    assert second is False
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_digest_refires_next_iso_week(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    digest = SQLiteWeeklyDigestStateRepository(session_factory)
    await tickets.create(_bug())
    job = WeeklyDigestJob(
        tickets=tickets,
        sla_state=sla,
        digest_state=digest,
        slack=fake_slack,
        digest_channel_id="C_SE_TICKETS",
        se_timezone="UTC",
    )
    await job.execute(now_utc=_MONDAY_0900_UTC)
    fake_slack.blocks_posted.clear()

    next_week = await job.execute(now_utc=_NEXT_MONDAY_0900_UTC)
    assert next_week is True
    assert len(fake_slack.blocks_posted) == 1


@pytest.mark.asyncio
async def test_digest_skips_when_channel_unconfigured(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    digest = SQLiteWeeklyDigestStateRepository(session_factory)
    await tickets.create(_bug())
    job = WeeklyDigestJob(
        tickets=tickets,
        sla_state=sla,
        digest_state=digest,
        slack=fake_slack,
        digest_channel_id=None,
        se_timezone="UTC",
    )
    fired = await job.execute(now_utc=_MONDAY_0900_UTC)
    assert fired is False
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_digest_counts_breach_rate_from_sla_dm_state(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    digest = SQLiteWeeklyDigestStateRepository(session_factory)
    p2_red = await tickets.create(_bug(priority=Priority.P2))
    p2_green = await tickets.create(_bug(priority=Priority.P2))
    p1_amber = await tickets.create(_bug(priority=Priority.P1))
    assert p2_red.id is not None and p2_green.id is not None and p1_amber.id is not None
    # One red FIRST_RESPONSE → breached
    await sla.upsert(
        p2_red.id,
        SLAStage.FIRST_RESPONSE,
        SLAState.RED,
        last_dm_at=datetime(2026, 5, 28),
        now=datetime.now(UTC).replace(tzinfo=None),
    )
    await sla.upsert(
        p2_green.id,
        SLAStage.FIRST_RESPONSE,
        SLAState.GREEN,
        last_dm_at=None,
        now=datetime.now(UTC).replace(tzinfo=None),
    )
    await sla.upsert(
        p1_amber.id,
        SLAStage.FIRST_RESPONSE,
        SLAState.AMBER,
        last_dm_at=datetime(2026, 5, 30),
        now=datetime.now(UTC).replace(tzinfo=None),
    )

    job = WeeklyDigestJob(
        tickets=tickets,
        sla_state=sla,
        digest_state=digest,
        slack=fake_slack,
        digest_channel_id="C_SE_TICKETS",
        se_timezone="UTC",
    )
    await job.execute(now_utc=_MONDAY_0900_UTC)
    # 1 breached out of 3 live → 33%.
    _channel, blocks, _text = fake_slack.blocks_posted[0]
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    assert "33%" in rendered


# --- RenderTicketsBoard ------------------------------------------------------


@pytest.mark.asyncio
async def test_board_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    board = RenderTicketsBoard(tickets=tickets, orgs=orgs, workspace_url="https://test.slack.com")
    blocks = await board.execute()
    assert len(blocks) == 1
    assert "no live tickets" in blocks[0]["text"]["text"]


@pytest.mark.asyncio
async def test_board_groups_by_lane_and_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    se_new = await tickets.create(
        _bug(status=TicketStatus.NEW, lane=Lane.SE_ACTION, title="se-new")
    )
    se_inp = await tickets.create(
        _bug(
            status=TicketStatus.IN_PROGRESS,
            lane=Lane.SE_ACTION,
            title="se-in-progress",
            priority=Priority.P1,
        )
    )
    dev_inp = await tickets.create(
        _bug(status=TicketStatus.IN_PROGRESS, lane=Lane.DEV_ACTION, title="dev-in-progress")
    )
    # Closed shouldn't appear.
    await tickets.create(_bug(status=TicketStatus.CLOSED, title="closed"))
    assert se_new.id and se_inp.id and dev_inp.id
    await tickets.add_org(se_new.id, "acme")

    board = RenderTicketsBoard(tickets=tickets, orgs=orgs, workspace_url="https://test.slack.com")
    blocks = await board.execute()
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    # Lane labels present.
    assert "SE Action" in rendered
    assert "Dev Action" in rendered
    # All three live ticket titles present; the closed one isn't.
    assert "se-new" in rendered
    assert "se-in-progress" in rendered
    assert "dev-in-progress" in rendered
    assert "closed" not in rendered
    # The org name rendered for se_new (which has acme linked).
    assert "Acme" in rendered
