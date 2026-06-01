"""Integration tests for Chunk 8 — SLA state machine + auto-close + CSM nudges.

Covers:
- `SLAStateMachine` writes sla_dm_state, DMs SE on green→amber and amber→red,
  doesn't refire on unchanged state, and skips paused (awaiting customer) tickets.
- `AutoCloseAwaiting` closes awaiting>7d tickets, appends status + comms event,
  updates the card, DMs SE; fires CSM pre-close nudges at day 0/4/6 once each.
- Event-log `last_status_change_into` returns the most recent transition timestamp.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.sla.auto_close import AutoCloseAwaiting
from customerbot.application.sla.scan import SLAStateMachine
from customerbot.config import SLATarget, _default_sla_targets
from customerbot.data.repository.bot_state import SQLiteSLADMStateRepository
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _ts(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute)


def _bug(
    *,
    priority: Priority = Priority.P2,
    status: TicketStatus = TicketStatus.NEW,
    created_at: datetime | None = None,
    first_response_at: datetime | None = None,
    card_channel_id: str | None = None,
    card_message_ts: str | None = None,
) -> Ticket:
    return Ticket(
        title="x",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        priority=priority,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        created_at=created_at or _ts(2026, 6, 1, 9, 0),
        first_response_at=first_response_at,
        card_channel_id=card_channel_id,
        card_message_ts=card_message_ts,
    )


# --- last_status_change_into --------------------------------------------------


@pytest.mark.asyncio
async def test_last_status_change_into_returns_most_recent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    t = await tickets.create(_bug())
    assert t.id is not None

    earlier = _ts(2026, 6, 2, 10, 0)
    later = _ts(2026, 6, 5, 10, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U", earlier
    )
    # A reopen + re-awaiting cycle.
    await events.append_status_change(
        t.id, TicketStatus.AWAITING_CUSTOMER, TicketStatus.IN_PROGRESS, "U", earlier
    )
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U", later
    )

    found = await events.last_status_change_into(t.id, TicketStatus.AWAITING_CUSTOMER)
    assert found == later


@pytest.mark.asyncio
async def test_last_status_change_into_returns_none_when_absent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    t = await tickets.create(_bug())
    assert t.id is not None
    assert await events.last_status_change_into(t.id, TicketStatus.AWAITING_CUSTOMER) is None


# --- SLA scan ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sla_scan_fires_dm_on_first_response_breach(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)
    # P2 first_response target = 8h. Created 9h ago → red.
    t = await tickets.create(_bug(priority=Priority.P2, created_at=_ts(2026, 6, 1, 0, 0)))
    assert t.id is not None

    scan = SLAStateMachine(
        tickets=tickets,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    fired = await scan.execute(now=_ts(2026, 6, 1, 9, 0))
    # Both FIRST_RESPONSE and RESOLUTION evaluate; FIRST_RESPONSE is red (9h > 8h).
    # RESOLUTION P2 target = 120h, so 9h elapsed is still green.
    assert (t.id, SLAStage.FIRST_RESPONSE, SLAState.RED) in fired
    # At least one DM went to SE.
    assert any(user == "U_SE" for user, _, _ in fake_slack.dm_blocks_sent)


@pytest.mark.asyncio
async def test_sla_scan_does_not_refire_on_same_state(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)
    t = await tickets.create(_bug(priority=Priority.P2, created_at=_ts(2026, 6, 1, 0, 0)))
    assert t.id is not None

    scan = SLAStateMachine(
        tickets=tickets,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    first = await scan.execute(now=_ts(2026, 6, 1, 9, 0))
    fake_slack.dm_blocks_sent.clear()
    second = await scan.execute(now=_ts(2026, 6, 1, 9, 15))
    assert first  # at least the first-response breach fired
    assert second == []  # same state on both stages → no new DMs
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_sla_scan_fires_green_to_amber_transition(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)
    # P2 first_response = 8h (480 min). 50% threshold = 240 min = 4h.
    t = await tickets.create(_bug(priority=Priority.P2, created_at=_ts(2026, 6, 1, 0, 0)))
    assert t.id is not None

    scan = SLAStateMachine(
        tickets=tickets,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    # 2h in — green. No DM, but state recorded.
    await scan.execute(now=_ts(2026, 6, 1, 2, 0))
    assert fake_slack.dm_blocks_sent == []
    # 5h in — amber. Should fire.
    fired = await scan.execute(now=_ts(2026, 6, 1, 5, 0))
    amber_for_first_resp = [
        (tid, stage, state)
        for (tid, stage, state) in fired
        if stage == SLAStage.FIRST_RESPONSE and state == SLAState.AMBER
    ]
    assert len(amber_for_first_resp) == 1


@pytest.mark.asyncio
async def test_sla_scan_skips_awaiting_customer(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)
    t = await tickets.create(
        _bug(
            priority=Priority.P2,
            status=TicketStatus.AWAITING_CUSTOMER,
            created_at=_ts(2026, 5, 1, 0, 0),  # way over budget
        )
    )
    assert t.id is not None
    scan = SLAStateMachine(
        tickets=tickets,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    assert await scan.execute(now=_ts(2026, 6, 1, 12, 0)) == []
    assert fake_slack.dm_blocks_sent == []
    # No sla_dm_state rows written.
    assert await sla_state.get(t.id, SLAStage.FIRST_RESPONSE) is None


@pytest.mark.asyncio
async def test_sla_scan_skips_priorities_with_no_targets(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)
    t = await tickets.create(_bug(priority=Priority.P4, created_at=_ts(2026, 1, 1, 0, 0)))
    assert t.id is not None
    # P4: first_response_minutes=2880, status_update=None, resolution=None.
    # 5 months later → FIRST_RESPONSE definitely breached; STATUS_UPDATE/RESOLUTION
    # uncommitted → silent.
    scan = SLAStateMachine(
        tickets=tickets,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    fired = await scan.execute(now=_ts(2026, 6, 1, 0, 0))
    stages_fired = {stage for (_, stage, _) in fired}
    assert SLAStage.FIRST_RESPONSE in stages_fired
    assert SLAStage.STATUS_UPDATE not in stages_fired
    assert SLAStage.RESOLUTION not in stages_fired


# --- Auto-close --------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_close_closes_after_seven_days(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)

    t = await tickets.create(
        _bug(
            status=TicketStatus.AWAITING_CUSTOMER,
            card_channel_id="C_SE_TICKETS",
            card_message_ts="1700000000.000100",
        )
    )
    assert t.id is not None
    entered_at = _ts(2026, 5, 25, 9, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U_SE", entered_at
    )

    job = AutoCloseAwaiting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    closed, _ = await job.execute(now=_ts(2026, 6, 2, 9, 0))  # 8 days in awaiting
    assert closed == [t.id]

    refreshed = await tickets.get(t.id)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.CLOSED

    # Card was updated.
    assert any(ch == "C_SE_TICKETS" for (ch, _ts_, _blocks, _text) in fake_slack.messages_updated)
    # SE got the auto-close DM.
    assert any(user == "U_SE" for user, _, _ in fake_slack.dm_blocks_sent)


@pytest.mark.asyncio
async def test_auto_close_appends_status_and_comms_events(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    from sqlalchemy import select

    from customerbot.data.database import EventCommsLogRow, EventStatusChangeRow

    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)

    t = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert t.id is not None
    entered_at = _ts(2026, 5, 24, 9, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U_SE", entered_at
    )

    job = AutoCloseAwaiting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    await job.execute(now=_ts(2026, 6, 1, 9, 0))

    async with session_factory() as session:
        status_rows = list((await session.execute(select(EventStatusChangeRow))).scalars())
        comms_rows = list((await session.execute(select(EventCommsLogRow))).scalars())
    # Two status-change rows: the initial transition we seeded + the auto-close row.
    auto_close_rows = [r for r in status_rows if r.to_status == TicketStatus.CLOSED.value]
    assert len(auto_close_rows) == 1
    assert auto_close_rows[0].by_user_id is None
    assert "auto-close" in auto_close_rows[0].note

    auto_close_comms = [r for r in comms_rows if r.note == "auto-close-note"]
    assert len(auto_close_comms) == 1


@pytest.mark.asyncio
async def test_auto_close_does_not_close_before_seven_days(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)

    t = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert t.id is not None
    entered_at = _ts(2026, 5, 30, 9, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U_SE", entered_at
    )

    job = AutoCloseAwaiting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    closed, _ = await job.execute(now=_ts(2026, 6, 1, 9, 0))  # 2 days only
    assert closed == []
    refreshed = await tickets.get(t.id)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.AWAITING_CUSTOMER


# --- CSM pre-close nudges ----------------------------------------------------


@pytest.mark.asyncio
async def test_csm_nudge_fires_at_day_zero_to_csm_of_affected_org(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)

    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    t = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert t.id is not None
    await tickets.add_org(t.id, "acme")
    entered_at = _ts(2026, 6, 1, 9, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U_SE", entered_at
    )

    job = AutoCloseAwaiting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    _, fired = await job.execute(now=_ts(2026, 6, 1, 9, 30))
    assert (t.id, SLAStage.AWAITING_NUDGE_7D) in fired
    # CSM was DM'd.
    assert any(user == "U_CSM" for user, _, _ in fake_slack.dm_blocks_sent)
    # The state row records "sent".
    assert await sla_state.get(t.id, SLAStage.AWAITING_NUDGE_7D) is not None


@pytest.mark.asyncio
async def test_csm_nudges_fire_once_then_skip(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)

    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    t = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert t.id is not None
    await tickets.add_org(t.id, "acme")
    entered_at = _ts(2026, 6, 1, 9, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U_SE", entered_at
    )

    job = AutoCloseAwaiting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    await job.execute(now=_ts(2026, 6, 1, 10, 0))  # day 0 nudge fires
    fake_slack.dm_blocks_sent.clear()
    _, fired = await job.execute(now=_ts(2026, 6, 1, 23, 0))  # still day 0
    assert fired == []
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_csm_nudge_three_day_marker_fires_at_day_four(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)

    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    t = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert t.id is not None
    await tickets.add_org(t.id, "acme")
    entered_at = _ts(2026, 6, 1, 9, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U_SE", entered_at
    )

    job = AutoCloseAwaiting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    _, day0 = await job.execute(now=_ts(2026, 6, 1, 10, 0))
    _, day4 = await job.execute(now=_ts(2026, 6, 5, 10, 0))
    assert any(stage == SLAStage.AWAITING_NUDGE_7D for (_, stage) in day0)
    assert any(stage == SLAStage.AWAITING_NUDGE_3D for (_, stage) in day4)


@pytest.mark.asyncio
async def test_csm_nudge_falls_back_to_se_when_no_csm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    sla_state = SQLiteSLADMStateRepository(session_factory)

    # Org exists but has no CSM assigned.
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id=None))
    t = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert t.id is not None
    await tickets.add_org(t.id, "acme")
    entered_at = _ts(2026, 6, 1, 9, 0)
    await events.append_status_change(
        t.id, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER, "U_SE", entered_at
    )

    job = AutoCloseAwaiting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        sla_state=sla_state,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    await job.execute(now=_ts(2026, 6, 1, 10, 0))
    assert any(user == "U_SE" for user, _, _ in fake_slack.dm_blocks_sent)


# Silence unused-import warnings.
_ = SLATarget
_ = timedelta
