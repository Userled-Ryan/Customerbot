"""Linear outbound hooks on the lifecycle handlers (Chunk C).

Asserts each ticket-card action pushes the right thing to Linear: direct
resolve → Done (silent), drop → Canceled, hotfix → underlying bug mirrored as
an open dev issue, move-to-dev → issue opened + added to the project. Uses the
real SQLite repos + FakeSlackPort + FakeLinearPort.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.lane_handoff import MoveToDevAction
from customerbot.application.tracking.resolve import ResolveTicket
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


def _bug(*, lane: Lane | None = Lane.SE_ACTION) -> Ticket:
    return Ticket(
        title="checkout broken on safari",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=TicketStatus.NEW,
        lane=lane,
        severity=Severity.BLOCKING,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="hang at submit",
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
    session_factory: async_sessionmaker[AsyncSession], *, ticket: Ticket
) -> _Seeded:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    created = await tickets.create(ticket)
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    fake_linear = FakeLinearPort()
    # Pre-mirror so the action operates on an existing issue.
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)
    return tickets, events, orgs, sync, fake_linear, created


@pytest.mark.asyncio
async def test_direct_resolve_closes_linear_silently(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_bug()
    )
    resolve = ResolveTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE", linear=sync
    )

    await resolve.execute(ticket_id=created.id or 0, by_user_id="U_SE")

    # Linear issue moved to Done (silently — no comment / dev alert via Linear).
    assert ("lin_1", LinearWorkflowState.DONE) in fake_linear.state_updates
    assert fake_linear.comments == []


@pytest.mark.asyncio
async def test_drop_cancels_in_linear(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_bug()
    )
    drop = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=fake_slack, linear=sync)

    await drop.execute(ticket_id=created.id or 0, by_user_id="U_SE")

    assert ("lin_1", LinearWorkflowState.CANCELED) in fake_linear.state_updates


@pytest.mark.asyncio
async def test_resolve_via_hotfix_mirrors_underlying_bug_as_open_dev_issue(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_bug()
    )
    resolve = ResolveTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE", linear=sync
    )

    result = await resolve.execute(
        ticket_id=created.id or 0, by_user_id="U_SE", via_hotfix=True
    )

    # Original closed Done; the auto-created underlying bug is a 2nd Linear issue,
    # opened for dev and added to the Product Responder project.
    assert result.linked_bug is not None
    assert len(fake_linear.created_issues) == 2
    assert ("lin_2", LinearWorkflowState.IN_PROGRESS) in fake_linear.state_updates
    assert "lin_2" in fake_linear.project_adds


@pytest.mark.asyncio
async def test_sync_to_linear_false_skips_outbound(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Inbound-driven transitions must not echo a write back to Linear."""
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_bug()
    )
    resolve = ResolveTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE", linear=sync
    )

    await resolve.execute(ticket_id=created.id or 0, by_user_id="U_SE", sync_to_linear=False)

    assert fake_linear.state_updates == []


@pytest.mark.asyncio
async def test_reopen_pushes_state_back_to_linear(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    from customerbot.application.tracking.reopen import ReopenTicket

    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_bug()
    )
    # Drop it first (closes the Linear issue to Canceled).
    drop = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=fake_slack, linear=sync)
    await drop.execute(ticket_id=created.id or 0, by_user_id="U_SE")
    fake_linear.state_updates.clear()

    reopen = ReopenTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE", linear=sync
    )
    await reopen.execute(ticket_id=created.id or 0, by_user_id="U_SE")

    # Reopened SE-lane ticket → mirror pushed back to In Progress (not left Canceled).
    assert ("lin_1", LinearWorkflowState.IN_PROGRESS) in fake_linear.state_updates


@pytest.mark.asyncio
async def test_move_to_dev_opens_issue_and_adds_to_project(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, events, orgs, sync, fake_linear, created = await _seed(
        session_factory, ticket=_bug(lane=Lane.SE_ACTION)
    )
    move = MoveToDevAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",
        support_ping_channel_id="C_SUPPORT",
        linear=sync,
    )

    await move.execute(ticket_id=created.id or 0, by_user_id="U_SE")

    assert ("lin_1", LinearWorkflowState.IN_PROGRESS) in fake_linear.state_updates
    assert "lin_1" in fake_linear.project_adds
