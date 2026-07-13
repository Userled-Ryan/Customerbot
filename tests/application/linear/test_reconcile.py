"""ReconcileLinear — repairs drift in both directions (no-desync backstop)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.linear.inbound import LinearInboundHandler
from customerbot.application.linear.reconcile import ReconcileLinear
from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.drop import DropTicket
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


def _bug(*, lane: Lane | None = Lane.DEV_ACTION) -> Ticket:
    return Ticket(
        title="checkout broken",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=TicketStatus.IN_PROGRESS,
        lane=lane,
        severity=Severity.BLOCKING,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        card_channel_id="C_SE_TICKETS",
        card_message_ts="1700000000.000100",
    )


def _reconciler(
    session_factory: async_sessionmaker[AsyncSession], fake_linear: FakeLinearPort
) -> tuple[ReconcileLinear, SQLiteTicketRepository, SQLiteOrgRepository, LinearSync]:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    slack = FakeSlackPort()
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    drop = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=slack, linear=sync)
    resolve = ResolveTicket(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        se_user_id="U_SE",
        linear=sync,
    )
    inbound = LinearInboundHandler(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        drop_ticket=drop,
        resolve_ticket=resolve,
        linear=fake_linear,
        se_user_id="U_SE",
        actor_id="U_BOT",
    )
    reconcile = ReconcileLinear(tickets=tickets, linear=fake_linear, sync=sync, inbound=inbound)
    return reconcile, tickets, orgs, sync


@pytest.mark.asyncio
async def test_reconcile_creates_missing_mirror(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_linear = FakeLinearPort()
    reconcile, tickets, orgs, _sync = _reconciler(session_factory, fake_linear)
    await orgs.upsert(Org(id="acme", name="Acme"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    # No mirror yet (simulating a dropped outbound write).
    assert created.linear_issue_id is None

    await reconcile.execute()

    repaired = await tickets.get(created.id)
    assert repaired is not None and repaired.linear_issue_id is not None


@pytest.mark.asyncio
async def test_reconcile_pulls_missed_dev_resolution(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_linear = FakeLinearPort()
    reconcile, tickets, orgs, sync = _reconciler(session_factory, fake_linear)
    await orgs.upsert(Org(id="acme", name="Acme"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    await sync.mirror_new_ticket(created)
    refreshed = await tickets.get(created.id)
    assert refreshed is not None and refreshed.linear_issue_id is not None

    # Dev moved it to Done in Linear but the webhook was missed.
    fake_linear.issue_states[refreshed.linear_issue_id] = LinearWorkflowState.DONE

    await reconcile.execute()

    repaired = await tickets.get(created.id)
    # Replaying the missed Done resolves the ticket (terminal), same as a live webhook.
    assert repaired is not None and repaired.status == TicketStatus.RESOLVED
