from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Severity,
    Source,
    TicketLinkRelation,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _bug_ticket() -> Ticket:
    return Ticket(
        title="Publishing fails on Safari",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        severity=Severity.BLOCKING,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        original_slack_link="https://x.slack.com/archives/C1/p123",
        description="Crashes on iOS 18.",
    )


@pytest.mark.asyncio
async def test_create_assigns_id_and_round_trips(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteTicketRepository(session_factory)
    created = await repo.create(_bug_ticket())

    assert created.id is not None
    assert created.display_id == f"TIC-{created.id:03d}"

    got = await repo.get(created.id)
    assert got is not None
    assert got.title == "Publishing fails on Safari"
    assert got.type == TicketType.BUG
    assert got.subtype == TicketSubtype.PLATFORM_WIDE
    assert got.severity == Severity.BLOCKING
    assert got.status == TicketStatus.NEW
    assert got.lane is None


@pytest.mark.asyncio
async def test_update_status_to_in_progress_sets_first_response_at(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteTicketRepository(session_factory)
    created = await repo.create(_bug_ticket())
    assert created.id is not None

    now = _utcnow()
    await repo.update_status(created.id, TicketStatus.IN_PROGRESS, now=now)

    updated = await repo.get(created.id)
    assert updated is not None
    assert updated.status == TicketStatus.IN_PROGRESS
    assert updated.first_response_at is not None


@pytest.mark.asyncio
async def test_update_status_in_progress_idempotent_on_first_response(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """first_response_at must only be set the FIRST time we leave New."""
    repo = SQLiteTicketRepository(session_factory)
    created = await repo.create(_bug_ticket())
    assert created.id is not None

    first = _utcnow()
    await repo.update_status(created.id, TicketStatus.IN_PROGRESS, now=first)
    await repo.update_status(created.id, TicketStatus.AWAITING_CUSTOMER, now=_utcnow())
    later = _utcnow()
    await repo.update_status(created.id, TicketStatus.IN_PROGRESS, now=later)

    updated = await repo.get(created.id)
    assert updated is not None and updated.first_response_at is not None
    # The first-response timestamp should match the *first* transition, not the second.
    assert (updated.first_response_at - first).total_seconds() < 0.001


@pytest.mark.asyncio
async def test_query_live_excludes_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteTicketRepository(session_factory)
    open_t = await repo.create(_bug_ticket())
    closed_t = await repo.create(_bug_ticket())
    assert open_t.id and closed_t.id

    await repo.update_status(closed_t.id, TicketStatus.CLOSED, now=_utcnow())

    live = await repo.query_live()
    ids = {t.id for t in live}
    assert open_t.id in ids
    assert closed_t.id not in ids


@pytest.mark.asyncio
async def test_add_org_and_list_orgs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    await orgs.upsert(Org(id="globex", name="Globex"))

    tickets = SQLiteTicketRepository(session_factory)
    t = await tickets.create(_bug_ticket())
    assert t.id is not None

    await tickets.add_org(t.id, "acme")
    await tickets.add_org(t.id, "globex")
    # Idempotent — adding the same org again should not raise.
    await tickets.add_org(t.id, "acme")

    listed = await tickets.list_orgs(t.id)
    assert sorted(listed) == ["acme", "globex"]


@pytest.mark.asyncio
async def test_add_link_hotfix(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    hotfix = await tickets.create(_bug_ticket())
    underlying = await tickets.create(_bug_ticket())
    assert hotfix.id and underlying.id

    await tickets.add_link(hotfix.id, underlying.id, TicketLinkRelation.HOTFIX_OF)
    # Idempotent
    await tickets.add_link(hotfix.id, underlying.id, TicketLinkRelation.HOTFIX_OF)


@pytest.mark.asyncio
async def test_find_by_slack_link(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteTicketRepository(session_factory)
    await repo.create(_bug_ticket())

    hit = await repo.find_by_slack_link("https://x.slack.com/archives/C1/p123")
    miss = await repo.find_by_slack_link("https://x.slack.com/archives/C1/pZZZ")
    assert hit is not None
    assert miss is None


@pytest.mark.asyncio
async def test_update_lane_and_card_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteTicketRepository(session_factory)
    t = await repo.create(_bug_ticket())
    assert t.id is not None

    await repo.update_lane(t.id, Lane.DEV_ACTION, now=_utcnow())
    await repo.update_card_message(t.id, "C_SE_TICKETS", "1700000000.123456")

    got = await repo.get(t.id)
    assert got is not None
    assert got.lane == Lane.DEV_ACTION
    assert got.card_channel_id == "C_SE_TICKETS"
    assert got.card_message_ts == "1700000000.123456"


@pytest.mark.asyncio
async def test_update_priority(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteTicketRepository(session_factory)
    t = await repo.create(_bug_ticket())
    assert t.id is not None

    await repo.update_priority(t.id, Priority.P1, now=_utcnow())

    got = await repo.get(t.id)
    assert got is not None
    assert got.priority == Priority.P1


@pytest.mark.asyncio
async def test_set_and_find_linear_issue(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteTicketRepository(session_factory)
    t = await repo.create(_bug_ticket())
    assert t.id is not None
    # Freshly-created tickets have no Linear mirror yet.
    assert t.linear_issue_id is None

    await repo.set_linear_issue(
        t.id,
        issue_id="lin_abc123",
        identifier="PRD-7",
        url="https://linear.app/userledio/issue/PRD-7",
    )

    got = await repo.get(t.id)
    assert got is not None
    assert got.linear_issue_id == "lin_abc123"
    assert got.linear_issue_identifier == "PRD-7"
    assert got.linear_issue_url == "https://linear.app/userledio/issue/PRD-7"

    hit = await repo.find_by_linear_issue_id("lin_abc123")
    miss = await repo.find_by_linear_issue_id("lin_nope")
    assert hit is not None and hit.id == t.id
    assert miss is None
