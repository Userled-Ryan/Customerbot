"""Integration tests for Chunk 9 — interactive ticket-card lifecycle buttons.

Covers `MoveToDevAction`, `ResolveTicket` (terminal resolve + resolution
capture + CSM alert), `ReopenTicket` (with the 30-day window enforcement),
and the two-step `Add affected org` flow. Each test exercises the use case
through the real SQLite repositories (via the `session_factory` conftest
fixture) and the `FakeSlackPort` recorder so we can assert on the actual Slack
side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.add_affected_org import (
    OpenAddOrgModal,
    SubmitAddAffectedOrg,
)
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.lane_handoff import MoveToDevAction
from customerbot.application.tracking.reopen import REOPEN_WINDOW, ReopenTicket
from customerbot.application.tracking.resolve import ResolveTicket
from customerbot.data.database import (
    EventCommsLogRow,
    EventStatusChangeRow,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    ResolutionType,
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
    status: TicketStatus = TicketStatus.NEW,
    lane: Lane | None = Lane.SE_ACTION,
    priority: Priority = Priority.P2,
    title: str = "checkout broken on safari",
    description: str = "users report a hang at submit step",
    card_channel_id: str | None = "C_SE_TICKETS",
    card_message_ts: str | None = "1700000000.000100",
    closed_at: datetime | None = None,
    feature: str | None = None,
) -> Ticket:
    return Ticket(
        title=title,
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        lane=lane,
        priority=priority,
        feature=feature,
        description=description,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        original_slack_link="https://test.slack.com/archives/C/p123",
        card_channel_id=card_channel_id,
        card_message_ts=card_message_ts,
        closed_at=closed_at,
        created_at=_ts(2026, 6, 1),
    )


# --- Move to Dev Action ------------------------------------------------------


@pytest.mark.asyncio
async def test_move_to_dev_action_flips_lane_and_pings_support(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    created = await tickets.create(_bug(lane=Lane.SE_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = MoveToDevAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",
        support_ping_channel_id="C_SUPPORT",
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result is not None
    assert result.lane == Lane.DEV_ACTION

    # @support was pinged in the support channel.
    assert any(ch == "C_SUPPORT" for ch, _blocks, _text in fake_slack.blocks_posted)
    # Card was updated to reflect new lane.
    assert any(ch == "C_SE_TICKETS" for ch, _ts_, _blocks, _text in fake_slack.messages_updated)
    # Comms log captured the handoff.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    handoff_rows = [c for c in comms if c.note == "lane-handoff:se->dev"]
    assert len(handoff_rows) == 1
    assert handoff_rows[0].channel == "C_SUPPORT"


@pytest.mark.asyncio
async def test_move_to_dev_action_without_support_channel_skips_ping(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug())
    assert created.id is not None

    use_case = MoveToDevAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle=None,
        support_ping_channel_id=None,
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result is not None and result.lane == Lane.DEV_ACTION
    # No support ping went out.
    assert fake_slack.blocks_posted == []
    # Card still refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _ts_, _blocks, _text in fake_slack.messages_updated)


# --- Resolved (terminal) ----------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_is_terminal_captures_resolution_and_alerts_csm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    created = await tickets.create(_bug(status=TicketStatus.IN_PROGRESS))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = ResolveTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    result = await use_case.execute(
        ticket_id=created.id,
        by_user_id="U_SE",
        resolution_type=ResolutionType.CODE_CHANGE,
        resolution_pr_link="https://github.com/x/y/pull/1",
    )
    assert result.ticket is not None
    # Resolved is terminal — straight to RESOLVED, not AWAITING_CUSTOMER.
    assert result.ticket.status == TicketStatus.RESOLVED
    assert result.ticket.resolution_type == ResolutionType.CODE_CHANGE
    assert result.ticket.resolution_pr_link == "https://github.com/x/y/pull/1"
    assert result.ticket.resolved_at is not None

    # Status-change event row records how it was resolved + PR link.
    async with session_factory() as session:
        rows = list((await session.execute(select(EventStatusChangeRow))).scalars())
    resolved_rows = [r for r in rows if r.to_status == TicketStatus.RESOLVED.value]
    assert len(resolved_rows) == 1
    assert resolved_rows[0].note == "resolved (code-change) — https://github.com/x/y/pull/1"

    # Resolved is terminal, so it drops out of the live set (no more SLA/nudges).
    live_ids = [t.id for t in await tickets.query_live()]
    assert created.id not in live_ids

    # SE got the §9c resolution draft DM; the org's CSM got the alert DM.
    dm_users = [user for user, _, _ in fake_slack.dm_blocks_sent]
    assert "U_SE" in dm_users
    assert "U_CSM" in dm_users
    # Card refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)


@pytest.mark.asyncio
async def test_resolved_is_noop_when_already_resolved(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.RESOLVED))
    assert created.id is not None

    use_case = ResolveTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    result = await use_case.execute(
        ticket_id=created.id,
        by_user_id="U_SE",
        resolution_type=ResolutionType.NO_CODE_CHANGE,
    )
    assert result.ticket is not None
    assert result.ticket.status == TicketStatus.RESOLVED

    # No card refresh and no DM since nothing actually changed.
    assert fake_slack.messages_updated == []
    assert fake_slack.dm_blocks_sent == []


# --- Reopen ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reopen_within_window_returns_ticket_to_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    # Closed 5 days ago — well inside the 30-day window.
    closed_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=5)
    created = await tickets.create(_bug(status=TicketStatus.CLOSED, closed_at=closed_at))
    assert created.id is not None

    use_case = ReopenTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result.reopened is True
    assert result.suggested_new_ticket is False
    assert result.ticket is not None
    assert result.ticket.status == TicketStatus.IN_PROGRESS

    # Card refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)
    # No DM (stale path only DMs).
    assert fake_slack.dm_blocks_sent == []
    # Event row recorded the reopen.
    async with session_factory() as session:
        rows = list((await session.execute(select(EventStatusChangeRow))).scalars())
    reopen_rows = [
        r
        for r in rows
        if r.from_status == TicketStatus.CLOSED.value
        and r.to_status == TicketStatus.IN_PROGRESS.value
    ]
    assert len(reopen_rows) == 1
    assert reopen_rows[0].note == "reopened-within-30d"


@pytest.mark.asyncio
async def test_reopen_outside_window_suggests_new_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    # Closed 45 days ago — outside the window.
    closed_at = datetime.now(UTC).replace(tzinfo=None) - (REOPEN_WINDOW + timedelta(days=15))
    created = await tickets.create(_bug(status=TicketStatus.CLOSED, closed_at=closed_at))
    assert created.id is not None

    use_case = ReopenTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result.reopened is False
    assert result.suggested_new_ticket is True
    # Ticket stays closed.
    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.CLOSED
    # Card was NOT refreshed.
    assert fake_slack.messages_updated == []
    # SE got the suggestion DM.
    assert any(user == "U_SE" for user, _, _ in fake_slack.dm_blocks_sent)


@pytest.mark.asyncio
async def test_reopen_noop_on_non_closed_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.IN_PROGRESS))
    assert created.id is not None

    use_case = ReopenTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result.reopened is False
    assert result.suggested_new_ticket is False


# --- Drop (manual close) -----------------------------------------------------


@pytest.mark.asyncio
async def test_drop_closes_ticket_and_refreshes_card(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    created = await tickets.create(_bug(status=TicketStatus.IN_PROGRESS))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=fake_slack)
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")

    assert result.dropped is True
    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    assert refreshed.status == TicketStatus.CLOSED
    assert refreshed.closed_at is not None
    # Card was re-rendered to its retired state.
    assert any(ch == "C_SE_TICKETS" for ch, _ts_, _blocks, _text in fake_slack.messages_updated)
    # The org's CSM was DM'd that the ticket was dropped.
    assert any(user == "U_CSM" for user, _, _ in fake_slack.dm_blocks_sent)


@pytest.mark.asyncio
async def test_dropped_ticket_leaves_the_live_set(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """The whole point of drop: a closed ticket isn't 'live', so the nudge /
    SLA jobs (which only scan query_live) stop touching it."""
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert created.id is not None

    use_case = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=fake_slack)
    await use_case.execute(ticket_id=created.id, by_user_id="U_SE")

    live_ids = [t.id for t in await tickets.query_live()]
    assert created.id not in live_ids


@pytest.mark.asyncio
async def test_drop_is_noop_when_already_closed(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.CLOSED, closed_at=_ts(2026, 6, 2)))
    assert created.id is not None

    use_case = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=fake_slack)
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result.dropped is False


# --- Add affected org --------------------------------------------------------


class _StubBumpCheck:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def execute(self, ticket_id: int) -> None:
        self.calls.append(ticket_id)


def _add_org_view_builder(
    orgs: list[Org], *, private_metadata: str, excluded_org_ids: set[str] | None = None
) -> dict[str, Any]:
    excluded = excluded_org_ids or set()
    available = [o for o in orgs if o.id not in excluded]
    return {
        "type": "modal",
        "callback_id": "add_affected_org",
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Add affected org"},
        "blocks": [{"type": "section", "text": {"type": "plain_text", "text": str(available)}}],
    }


@pytest.mark.asyncio
async def test_open_add_org_modal_excludes_already_linked_orgs(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    await orgs.upsert(Org(id="globex", name="Globex"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = OpenAddOrgModal(
        slack=fake_slack,
        orgs=orgs,
        tickets=tickets,
        view_builder=_add_org_view_builder,
    )
    await use_case.execute(trigger_id="T1", ticket_id=created.id)
    assert len(fake_slack.views_opened) == 1
    _trigger, view = fake_slack.views_opened[0]
    # private_metadata carries the ticket id so the submission handler can route.
    assert view["private_metadata"] == str(created.id)
    # The stub view-builder stringified the available list — verify acme was excluded.
    rendered = view["blocks"][0]["text"]["text"]
    assert "globex" in rendered
    assert "acme" not in rendered


@pytest.mark.asyncio
async def test_submit_add_affected_org_links_and_triggers_bump_check(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    await orgs.upsert(Org(id="globex", name="Globex"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    stub_bump = _StubBumpCheck()
    use_case = SubmitAddAffectedOrg(
        slack=fake_slack, tickets=tickets, orgs=orgs, bump_check=stub_bump
    )
    org = await use_case.execute(ticket_id=created.id, org_id="globex", by_user_id="U_SE")
    assert org is not None and org.id == "globex"

    linked = await tickets.list_orgs(created.id)
    assert set(linked) == {"acme", "globex"}

    # Bump check triggered exactly once for this ticket.
    assert stub_bump.calls == [created.id]
    # Card refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)


@pytest.mark.asyncio
async def test_submit_add_affected_org_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    stub_bump = _StubBumpCheck()
    use_case = SubmitAddAffectedOrg(
        slack=fake_slack, tickets=tickets, orgs=orgs, bump_check=stub_bump
    )
    await use_case.execute(ticket_id=created.id, org_id="acme", by_user_id="U_SE")

    linked = await tickets.list_orgs(created.id)
    assert linked == ["acme"]
    # No bump check fires when nothing changed.
    assert stub_bump.calls == []
    # No card refresh on no-op.
    assert fake_slack.messages_updated == []
