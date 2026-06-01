from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import EventStatusChangeRow
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    CommsDirection,
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ticket() -> Ticket:
    return Ticket(
        title="x",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        severity=Severity.BLOCKING,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
    )


@pytest.mark.asyncio
async def test_append_status_change(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)

    t = await tickets.create(_ticket())
    assert t.id is not None

    await events.append_status_change(
        ticket_id=t.id,
        from_status=None,
        to_status=TicketStatus.NEW,
        by_user_id=None,
        at=_utcnow(),
        note="ticket created",
    )


@pytest.mark.asyncio
async def test_append_prio_reclass_comms(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    t = await tickets.create(_ticket())
    assert t.id is not None

    now = _utcnow()
    await events.append_prio_change(
        ticket_id=t.id,
        from_priority=None,
        to_priority=Priority.P2,
        by_user_id=None,
        at=now,
        reason="matrix lookup",
    )
    await events.append_reclassification(
        ticket_id=t.id,
        from_type=TicketType.BUG,
        to_type=TicketType.CONFIG,
        from_subtype=TicketSubtype.PLATFORM_WIDE,
        to_subtype=TicketSubtype.SETUP_INTEGRATION,
        by_user_id="U_SE",
        at=now,
        reason="customer permissions issue",
        next_step="customer to update Salesforce permissions",
        owner_user_id="U_CSM",
    )
    await events.append_comms(
        ticket_id=t.id,
        direction=CommsDirection.OUTBOUND,
        channel="C_ACME",
        sender_user_id="U_SE",
        message_link="https://x.slack.com/p999",
        at=now,
    )


@pytest.mark.asyncio
async def test_event_log_rejects_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The migration installs SQLite triggers that abort UPDATE on event-log tables."""
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    t = await tickets.create(_ticket())
    assert t.id is not None
    await events.append_status_change(
        ticket_id=t.id,
        from_status=None,
        to_status=TicketStatus.NEW,
        by_user_id=None,
        at=_utcnow(),
    )

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(
                update(EventStatusChangeRow)
                .where(EventStatusChangeRow.ticket_id == t.id)
                .values(note="tampered")
            )
            await session.commit()


@pytest.mark.asyncio
async def test_event_log_rejects_delete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    t = await tickets.create(_ticket())
    assert t.id is not None
    await events.append_status_change(
        ticket_id=t.id,
        from_status=None,
        to_status=TicketStatus.NEW,
        by_user_id=None,
        at=_utcnow(),
    )

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(
                delete(EventStatusChangeRow).where(EventStatusChangeRow.ticket_id == t.id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_append_only_triggers_exist_on_all_four_tables(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sanity check that all four event-log tables got UPDATE+DELETE triggers."""
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
        )
        trigger_names = [r[0] for r in result.all()]

    expected = {
        f"prevent_{table}_{op}"
        for table in (
            "event_status_changes",
            "event_prio_changes",
            "event_reclassifications",
            "event_comms_log",
        )
        for op in ("update", "delete")
    }
    assert expected.issubset(set(trigger_names))
