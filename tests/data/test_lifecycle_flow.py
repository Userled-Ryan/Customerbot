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

from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.add_affected_org import (
    OpenAddOrgModal,
    SubmitAddAffectedOrg,
)
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.lane_handoff import (
    DEV_HANDOFF_CUSTOMER_REPLY,
    MoveToDevAction,
    ReturnToSEAction,
)
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
from tests.conftest import FakeLinearPort, FakeSlackPort


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
async def test_move_to_dev_action_flips_lane_and_dms_dev_on_support(
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
    # The support user-group is the dev(s) on duty — DM every member.
    fake_slack.user_group_memberships["S0123ABCD"] = {"U_DEV1", "U_DEV2"}

    use_case = MoveToDevAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result is not None
    assert result.lane == Lane.DEV_ACTION

    # Every member of the support group was DM'd the handoff (no channel ping).
    dm_users = {user for user, _blocks, _text in fake_slack.dm_blocks_sent}
    assert dm_users == {"U_DEV1", "U_DEV2"}
    assert fake_slack.blocks_posted == []
    # The tickets feed shows the handoff: 🛠️ reaction on the card + threaded reply.
    assert (
        "C_SE_TICKETS",
        "1700000000.000100",
        "hammer_and_wrench",
    ) in fake_slack.reactions_added
    thread_replies = [
        (ch, thread_ts) for ch, text, thread_ts in fake_slack.messages_sent if "Dev Action" in text
    ]
    assert ("C_SE_TICKETS", "1700000000.000100") in thread_replies
    # Card was updated to reflect new lane.
    assert any(ch == "C_SE_TICKETS" for ch, _ts_, _blocks, _text in fake_slack.messages_updated)
    # Comms log captured the handoff.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    handoff_rows = [c for c in comms if c.note == "lane-handoff:se->dev"]
    assert len(handoff_rows) == 1
    assert handoff_rows[0].channel == "dm:dev-on-support"


@pytest.mark.asyncio
async def test_move_to_dev_action_without_support_group_skips_dm(
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
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result is not None and result.lane == Lane.DEV_ACTION
    # No dev DM went out (group not configured).
    assert fake_slack.dm_blocks_sent == []
    assert fake_slack.blocks_posted == []
    # Card still refreshed and the feed still marks the handoff.
    assert any(ch == "C_SE_TICKETS" for ch, _ts_, _blocks, _text in fake_slack.messages_updated)
    assert any(emoji == "hammer_and_wrench" for _ch, _ts, emoji in fake_slack.reactions_added)


@pytest.mark.asyncio
async def test_move_to_dev_action_puts_linear_issue_in_the_devs_name(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    bug = _bug(lane=Lane.SE_ACTION)
    bug.se_owner_user_id = "U_SE_OWNER"
    created = await tickets.create(bug)
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    fake_slack.user_group_memberships["S0123ABCD"] = {"U_DEV1", "U_DEV2"}
    # Only the second (sorted) member has a Linear user — assigning the other
    # would be a silent no-op, so that's the one that must be picked.
    fake_linear = FakeLinearPort()
    fake_linear.linear_to_slack = {"lin_user_dev2": "U_DEV2"}
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)

    use_case = MoveToDevAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",
        linear=sync,
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")

    assert result is not None
    assert result.dev_owner_user_id == "U_DEV2"
    # The Linear issue is now in the dev's name; the SE owner is left alone.
    assert fake_linear.assignments[-1] == ("lin_1", "U_DEV2")
    assert result.se_owner_user_id == "U_SE_OWNER"
    # The handoff DM and the card-thread reply both name the dev.
    dm_text = fake_slack.dm_blocks_sent[0][1][0]["text"]["text"]
    assert "<@U_DEV2>" in dm_text
    assert any("<@U_DEV2>" in text for _ch, text, _thread in fake_slack.messages_sent)


@pytest.mark.asyncio
async def test_move_to_dev_action_without_linear_still_records_a_dev(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    # Linear off: no mapping to consult, so the first member on duty is recorded.
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(lane=Lane.SE_ACTION))
    assert created.id is not None
    fake_slack.user_group_memberships["S0123ABCD"] = {"U_DEV1", "U_DEV2"}

    use_case = MoveToDevAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")

    assert result is not None and result.dev_owner_user_id == "U_DEV1"


@pytest.mark.asyncio
async def test_move_to_dev_action_without_support_group_records_no_dev(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(lane=Lane.SE_ACTION))
    assert created.id is not None

    use_case = MoveToDevAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",  # configured, but the group is empty
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")

    # Lane still flips — there's just nobody to name.
    assert result is not None and result.lane == Lane.DEV_ACTION
    assert result.dev_owner_user_id is None


@pytest.mark.asyncio
async def test_move_to_dev_action_tells_customer_thread_once(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """The customer was told "the team is taking a look" when the ticket was
    logged, so they get told when it moves to engineering — but a nudge-click on
    an already-handed-off ticket must not repeat itself in their thread. The
    internal support thread stays silent (it watches the card feed)."""
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", slack_channel_id="C_ACME"))
    created = await tickets.create(_bug(lane=Lane.SE_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    now = _ts(2026, 6, 2)
    await tickets.link_support_thread(created.id, "C_ACME", "900.9", by_user_id="U_SE", now=now)
    await tickets.link_support_thread(created.id, "C_SUPPORT", "800.8", by_user_id="U_SE", now=now)

    use_case = MoveToDevAction(
        tickets=tickets,
        events=SQLiteEventLogRepository(session_factory),
        orgs=orgs,
        slack=fake_slack,
        support_handle=None,
    )
    await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    await use_case.execute(ticket_id=created.id, by_user_id="U_SE")  # nudge — already on the lane

    handoffs = [
        (ch, thread_ts)
        for ch, text, thread_ts in fake_slack.messages_sent
        if text == DEV_HANDOFF_CUSTOMER_REPLY
    ]
    assert handoffs == [("C_ACME", "900.9")]


# --- Return to SE (undo dev handoff) ----------------------------------------


@pytest.mark.asyncio
async def test_return_to_se_flips_lane_back_and_notifies_dev(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    created = await tickets.create(_bug(lane=Lane.DEV_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    fake_slack.user_group_memberships["S0123ABCD"] = {"U_DEV1", "U_DEV2"}

    use_case = ReturnToSEAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result is not None
    assert result.lane == Lane.SE_ACTION

    # Every dev on support was told it's back with Solutions Eng.
    dm_users = {user for user, _blocks, _text in fake_slack.dm_blocks_sent}
    assert dm_users == {"U_DEV1", "U_DEV2"}
    dm_text = fake_slack.dm_blocks_sent[0][1][0]["text"]["text"]
    assert "~" in dm_text  # ticket reference struck through
    assert "Back with Solutions Eng" in dm_text
    # The 🛠️ handoff marker is cleared and the return noted in the card thread.
    assert (
        "C_SE_TICKETS",
        "1700000000.000100",
        "hammer_and_wrench",
    ) in fake_slack.reactions_removed
    assert any(
        ch == "C_SE_TICKETS" and "SE Action" in text
        for ch, text, _thread_ts in fake_slack.messages_sent
    )
    # Comms log captured the undo.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    undo_rows = [c for c in comms if c.note == "lane-handoff:dev->se"]
    assert len(undo_rows) == 1
    assert undo_rows[0].channel == "dm:dev-on-support"


@pytest.mark.asyncio
async def test_return_to_se_clears_dev_owner_and_hands_linear_back(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    bug = _bug(lane=Lane.DEV_ACTION)
    bug.se_owner_user_id = "U_SE_OWNER"
    bug.dev_owner_user_id = "U_DEV2"
    created = await tickets.create(bug)
    assert created.id is not None
    fake_slack.user_group_memberships["S0123ABCD"] = {"U_DEV2"}
    fake_linear = FakeLinearPort()
    fake_linear.linear_to_slack = {"lin_user_dev2": "U_DEV2", "lin_user_se": "U_SE_OWNER"}
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)

    use_case = ReturnToSEAction(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=fake_slack,
        support_handle="S0123ABCD",
        linear=sync,
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")

    assert result is not None and result.dev_owner_user_id is None
    # With no dev on it, the issue falls back to the SE owner.
    assert fake_linear.assignments[-1] == ("lin_1", "U_SE_OWNER")


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

    # Resolving is terminal — no §9c resolution-draft DM to the SE anymore;
    # only the org's CSM gets the terminal-state alert DM.
    dm_users = [user for user, _, _ in fake_slack.dm_blocks_sent]
    assert "U_SE" not in dm_users
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
async def test_reopen_resolved_ticket_returns_to_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    # A resolved ticket has no `closed_at`, so the 30-day window never applies —
    # clicking Reopen must clear the struck card and put it back In progress.
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.RESOLVED))
    assert created.id is not None

    use_case = ReopenTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    result = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert result.reopened is True
    assert result.suggested_new_ticket is False
    assert result.ticket is not None
    assert result.ticket.status == TicketStatus.IN_PROGRESS

    # Card refreshed (strikethrough cleared) and no stale-path DM.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)
    assert fake_slack.dm_blocks_sent == []
    # Event row recorded the reopen from RESOLVED.
    async with session_factory() as session:
        rows = list((await session.execute(select(EventStatusChangeRow))).scalars())
    reopen_rows = [
        r
        for r in rows
        if r.from_status == TicketStatus.RESOLVED.value
        and r.to_status == TicketStatus.IN_PROGRESS.value
    ]
    assert len(reopen_rows) == 1
    assert reopen_rows[0].note == "reopened (from resolved)"


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
