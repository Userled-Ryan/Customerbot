"""`MarkInProgressOnReply`: an assigned SE's reply in the ticket's origin thread
advances a New ticket to In Progress and mirrors that onto Linear.

Uses the real SQLite repos + FakeSlackPort + FakeLinearPort (via LinearSync).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.mark_in_progress_on_reply import (
    MarkInProgressOnReply,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeLinearPort, FakeSlackPort

_CHANNEL = "C_CUST"
_THREAD_TS = "1700000000.000200"
_SE_OWNER = "U_SE_OWNER"


def _ticket(
    *,
    slack: FakeSlackPort,
    status: TicketStatus = TicketStatus.NEW,
    se_owner: str | None = _SE_OWNER,
) -> Ticket:
    return Ticket(
        title="checkout broken on safari",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        lane=Lane.SE_ACTION,
        severity=Severity.BLOCKING,
        reporter_user_id="U_CSM",
        se_owner_user_id=se_owner,
        source=Source.CUSTOMER_CHANNEL,
        description="hang at submit",
        original_slack_link=slack.build_thread_link(_CHANNEL, _THREAD_TS),
        card_channel_id="C_SE_TICKETS",
        card_message_ts="1700000000.000100",
    )


_Seeded = tuple[
    SQLiteTicketRepository,
    SQLiteEventLogRepository,
    SQLiteOrgRepository,
    LinearSync,
    FakeLinearPort,
    Ticket,
]


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ticket: Ticket,
    mirror: bool = True,
) -> _Seeded:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    created = await tickets.create(ticket)
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    fake_linear = FakeLinearPort()
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    if mirror:
        await sync.mirror_new_ticket(created)
    return tickets, events, orgs, sync, fake_linear, created


def _use_case(
    tickets: SQLiteTicketRepository,
    events: SQLiteEventLogRepository,
    orgs: SQLiteOrgRepository,
    slack: FakeSlackPort,
    linear: LinearSync | None,
) -> MarkInProgressOnReply:
    return MarkInProgressOnReply(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        se_member_ids={_SE_OWNER, "U_OTHER_SE"},
        linear=linear,
    )


@pytest.mark.asyncio
async def test_assigned_se_reply_advances_new_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_ticket(slack=fake_slack)
    )
    use_case = _use_case(tickets, events, orgs, fake_slack, sync)

    fired = await use_case.execute(
        channel_id=_CHANNEL, thread_ts=_THREAD_TS, sender_user_id=_SE_OWNER
    )

    assert fired is True
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.IN_PROGRESS
    assert refreshed.first_response_at is not None
    # Card redrawn, and Linear mirror moved to In Progress.
    assert fake_slack.messages_updated  # refresh_card fired
    assert ("lin_1", LinearWorkflowState.IN_PROGRESS) in fake_linear.state_updates
    # A New → In Progress audit row was written.
    assert (
        await events.last_status_change_into(created.id or 0, TicketStatus.IN_PROGRESS) is not None
    )


@pytest.mark.asyncio
async def test_non_owner_se_reply_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_ticket(slack=fake_slack)
    )
    use_case = _use_case(tickets, events, orgs, fake_slack, sync)

    # An SE who is *not* the assigned owner replies.
    fired = await use_case.execute(
        channel_id=_CHANNEL, thread_ts=_THREAD_TS, sender_user_id="U_OTHER_SE"
    )

    assert fired is False
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.NEW
    assert fake_linear.state_updates == []


@pytest.mark.asyncio
async def test_non_se_reply_skips_before_lookup(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_ticket(slack=fake_slack)
    )
    use_case = _use_case(tickets, events, orgs, fake_slack, sync)

    # A CSM / customer (not in the SE candidate set) replies.
    fired = await use_case.execute(
        channel_id=_CHANNEL, thread_ts=_THREAD_TS, sender_user_id="U_CSM"
    )

    assert fired is False
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.NEW
    assert fake_linear.state_updates == []


@pytest.mark.asyncio
async def test_already_in_progress_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_ticket(slack=fake_slack, status=TicketStatus.IN_PROGRESS)
    )
    use_case = _use_case(tickets, events, orgs, fake_slack, sync)

    fired = await use_case.execute(
        channel_id=_CHANNEL, thread_ts=_THREAD_TS, sender_user_id=_SE_OWNER
    )

    assert fired is False
    assert fake_linear.state_updates == []


@pytest.mark.asyncio
async def test_resolved_ticket_is_not_reopened(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_ticket(slack=fake_slack, status=TicketStatus.RESOLVED)
    )
    use_case = _use_case(tickets, events, orgs, fake_slack, sync)

    fired = await use_case.execute(
        channel_id=_CHANNEL, thread_ts=_THREAD_TS, sender_user_id=_SE_OWNER
    )

    assert fired is False
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.RESOLVED
    assert fake_linear.state_updates == []


@pytest.mark.asyncio
async def test_reply_in_unrelated_thread_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_ticket(slack=fake_slack)
    )
    use_case = _use_case(tickets, events, orgs, fake_slack, sync)

    # Same assigned SE, but a thread that maps to no ticket.
    fired = await use_case.execute(
        channel_id=_CHANNEL, thread_ts="9999999999.999999", sender_user_id=_SE_OWNER
    )

    assert fired is False
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.NEW
    assert fake_linear.state_updates == []


@pytest.mark.asyncio
async def test_transitions_without_linear(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, _sync, _fake_linear, created = await _seed(
        session_factory, ticket=_ticket(slack=fake_slack), mirror=False
    )
    use_case = _use_case(tickets, events, orgs, fake_slack, linear=None)

    fired = await use_case.execute(
        channel_id=_CHANNEL, thread_ts=_THREAD_TS, sender_user_id=_SE_OWNER
    )

    assert fired is True
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.IN_PROGRESS
