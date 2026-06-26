"""LinearInboundHandler — applying dev Linear changes back into customerbot."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.linear.inbound import LinearInboundEvent, LinearInboundHandler
from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.drop import DropTicket
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

ACTOR_BOT = "U_BOT"


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
        description="hang at submit",
        card_channel_id="C_SE_TICKETS",
        card_message_ts="1700000000.000100",
    )


class _Harness:
    def __init__(
        self,
        tickets: SQLiteTicketRepository,
        slack: FakeSlackPort,
        linear: FakeLinearPort,
        inbound: LinearInboundHandler,
        ticket: Ticket,
    ) -> None:
        self.tickets = tickets
        self.slack = slack
        self.linear = linear
        self.inbound = inbound
        self.ticket = ticket


async def _harness(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lane: Lane | None = Lane.DEV_ACTION,
    with_csm: bool = True,
) -> _Harness:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    slack = FakeSlackPort()
    fake_linear = FakeLinearPort()
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM" if with_csm else None))
    created = await tickets.create(_bug(lane=lane))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)
    drop = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=slack, linear=sync)
    inbound = LinearInboundHandler(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        drop_ticket=drop,
        se_user_id="U_SE",
        actor_id=ACTOR_BOT,
    )
    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    # Clear the mirror's side effects so assertions see only inbound activity.
    fake_linear.state_updates.clear()
    slack.dm_blocks_sent.clear()
    return _Harness(tickets, slack, fake_linear, inbound, refreshed)


def _issue_event(
    h: _Harness, state: LinearWorkflowState, *, actor: str = "U_DEV"
) -> LinearInboundEvent:
    return LinearInboundEvent(
        entity_type="Issue",
        actor_id=actor,
        actor_name="Dana",
        issue_id=h.ticket.linear_issue_id or "",
        new_state=state,
    )


@pytest.mark.asyncio
async def test_dev_done_returns_to_se_without_echo(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None
    assert updated.status == TicketStatus.AWAITING_CUSTOMER
    # SE + stakeholder CSM both notified.
    recipients = {uid for uid, _b, _t in h.slack.dm_blocks_sent}
    assert "U_SE" in recipients
    assert "U_CSM" in recipients
    # No echo back to Linear (inbound transition uses sync_to_linear=False).
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_dev_canceled_closes_ticket(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.CANCELED))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.CLOSED
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_comment_notifies_only_no_status_change(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    event = LinearInboundEvent(
        entity_type="Comment",
        actor_id="U_DEV",
        actor_name="Dana",
        issue_id=h.ticket.linear_issue_id or "",
        comment_body="looking into it",
    )
    await h.inbound.handle(h.ticket, event)

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS  # unchanged
    assert {uid for uid, _b, _t in h.slack.dm_blocks_sent} == {"U_SE", "U_CSM"}


@pytest.mark.asyncio
async def test_self_actor_event_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE, actor=ACTOR_BOT))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS  # unchanged
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_se_lane_event_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory, lane=Lane.SE_ACTION)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS  # unchanged
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_done_is_idempotent_no_double_notify(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE))
    sent_after_first = len(h.slack.dm_blocks_sent)
    # Re-deliver the same Done (Linear retry / reconcile) — must be a no-op now.
    refreshed = await h.tickets.get(h.ticket.id or 0)
    assert refreshed is not None
    await h.inbound.handle(refreshed, _issue_event(h, LinearWorkflowState.DONE))
    assert len(h.slack.dm_blocks_sent) == sent_after_first
