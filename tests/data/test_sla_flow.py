"""Integration tests for Chunk 8 — SLA state machine.

Covers:
- `SLAStateMachine` writes sla_dm_state and returns amber/red transitions
  silently (no DMs — the open-tickets digest is the sole SE notification),
  doesn't refire on unchanged state, and skips paused (awaiting customer) tickets.
- Event-log `last_status_change_into` returns the most recent transition timestamp.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.sla.scan import SLAStateMachine
from customerbot.config import SLATarget, _default_sla_targets
from customerbot.data.repository.bot_state import SQLiteSLADMStateRepository
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.tickets.entities import Ticket
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
async def test_sla_scan_records_first_response_breach_silently(
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
        sla_targets=_default_sla_targets(),
    )
    fired = await scan.execute(now=_ts(2026, 6, 1, 9, 0))
    # Both FIRST_RESPONSE and RESOLUTION evaluate; FIRST_RESPONSE is red (9h > 8h).
    # RESOLUTION P2 target = 120h, so 9h elapsed is still green.
    assert (t.id, SLAStage.FIRST_RESPONSE, SLAState.RED) in fired
    # The clock state is persisted for reporting...
    record = await sla_state.get(t.id, SLAStage.FIRST_RESPONSE)
    assert record is not None and record.last_state == SLAState.RED
    # ...but the scan no longer DMs anyone — the digest is the sole notification.
    assert fake_slack.dm_blocks_sent == []


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
        sla_targets=_default_sla_targets(),
    )
    fired = await scan.execute(now=_ts(2026, 6, 1, 0, 0))
    stages_fired = {stage for (_, stage, _) in fired}
    assert SLAStage.FIRST_RESPONSE in stages_fired
    assert SLAStage.STATUS_UPDATE not in stages_fired
    assert SLAStage.RESOLUTION not in stages_fired


# Silence unused-import warnings.
_ = SLATarget
_ = timedelta
