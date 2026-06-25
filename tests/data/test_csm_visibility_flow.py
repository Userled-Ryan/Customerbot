"""CSM ticket visibility — Friday per-CSM digest + on-demand `/mytickets` core.

Covers:
- `CSMTicketsView` groups live tickets under the CSM who owns the affected
  org(s), scoped to that CSM's orgs, deduped across multiple owned orgs.
- `FridayCSMDigestJob` only fires inside the Friday 12:00 SE-local window,
  DMs each CSM only their customers' tickets, and throttles to once per
  ISO-week via the persisted bookmark.
- `render_csm_tickets_blocks` handles the empty and populated cases.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.csm_digest import FridayCSMDigestJob
from customerbot.application.tracking.csm_tickets import (
    CSMTicketsView,
    render_csm_tickets_blocks,
)
from customerbot.data.repository.bot_state import SQLiteCSMDigestStateRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort

# 2026-06-26 is a Friday; 12:30 UTC is inside the 12:00 fire hour for a UTC SE tz.
_FRIDAY_WINDOW_UTC = datetime(2026, 6, 26, 12, 30)
_FRIDAY_BEFORE_UTC = datetime(2026, 6, 26, 9, 0)
_THURSDAY_WINDOW_UTC = datetime(2026, 6, 25, 12, 30)

_CSM_A = "U_CSM_A"
_CSM_B = "U_CSM_B"


def _ticket(
    *,
    title: str = "checkout broken",
    ttype: TicketType = TicketType.BUG,
    priority: Priority = Priority.P2,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    reporter: str = "U_SE",
) -> Ticket:
    return Ticket(
        title=title,
        type=ttype,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        lane=Lane.SE_ACTION,
        priority=priority,
        reporter_user_id=reporter,
        source=Source.CUSTOMER_CHANNEL,
        description="...",
    )


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[SQLiteTicketRepository, SQLiteOrgRepository]:
    orgs = SQLiteOrgRepository(session_factory)
    # acme + globex owned by CSM A; initech owned by CSM B; nocsm has no CSM.
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id=_CSM_A))
    await orgs.upsert(Org(id="globex", name="Globex", csm_user_id=_CSM_A))
    await orgs.upsert(Org(id="initech", name="Initech", csm_user_id=_CSM_B))
    await orgs.upsert(Org(id="nocsm", name="NoCSM", csm_user_id=None))
    tickets = SQLiteTicketRepository(session_factory)
    return tickets, orgs


def _view(tickets: SQLiteTicketRepository, orgs: SQLiteOrgRepository) -> CSMTicketsView:
    return CSMTicketsView(tickets=tickets, orgs=orgs, workspace_url="https://test.slack.com")


# --- CSMTicketsView ---------------------------------------------------------


@pytest.mark.asyncio
async def test_groups_tickets_by_owning_csm(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets, orgs = await _seed(session_factory)
    t_acme = await tickets.create(_ticket(title="acme issue"))
    t_initech = await tickets.create(_ticket(title="initech issue", reporter=_CSM_B))
    await tickets.add_org(t_acme.id, "acme")  # type: ignore[arg-type]
    await tickets.add_org(t_initech.id, "initech")  # type: ignore[arg-type]

    grouped = await _view(tickets, orgs).tickets_by_csm()

    assert set(grouped) == {_CSM_A, _CSM_B}
    (a_ticket, a_orgs) = grouped[_CSM_A][0]
    assert a_ticket.title == "acme issue"
    assert a_orgs == ["Acme"]
    assert grouped[_CSM_B][0][0].title == "initech issue"


@pytest.mark.asyncio
async def test_ticket_spanning_two_owned_orgs_appears_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets, orgs = await _seed(session_factory)
    t = await tickets.create(_ticket(title="multi-org"))
    await tickets.add_org(t.id, "acme")  # type: ignore[arg-type]
    await tickets.add_org(t.id, "globex")  # type: ignore[arg-type]

    items = await _view(tickets, orgs).tickets_for_csm(_CSM_A)

    assert len(items) == 1
    ticket, org_names = items[0]
    assert ticket.title == "multi-org"
    assert set(org_names) == {"Acme", "Globex"}


@pytest.mark.asyncio
async def test_ticket_for_org_without_csm_is_not_surfaced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets, orgs = await _seed(session_factory)
    t = await tickets.create(_ticket())
    await tickets.add_org(t.id, "nocsm")  # type: ignore[arg-type]

    grouped = await _view(tickets, orgs).tickets_by_csm()

    assert grouped == {}


@pytest.mark.asyncio
async def test_closed_tickets_excluded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets, orgs = await _seed(session_factory)
    live = await tickets.create(_ticket(title="open"))
    closed = await tickets.create(_ticket(title="done", status=TicketStatus.CLOSED))
    await tickets.add_org(live.id, "acme")  # type: ignore[arg-type]
    await tickets.add_org(closed.id, "acme")  # type: ignore[arg-type]

    items = await _view(tickets, orgs).tickets_for_csm(_CSM_A)

    assert [t.title for t, _ in items] == ["open"]


# --- FridayCSMDigestJob -----------------------------------------------------


def _job(
    view: CSMTicketsView,
    fake_slack: FakeSlackPort,
    session_factory: async_sessionmaker[AsyncSession],
) -> FridayCSMDigestJob:
    return FridayCSMDigestJob(
        view=view,
        digest_state=SQLiteCSMDigestStateRepository(session_factory),
        slack=fake_slack,
        se_timezone="UTC",
    )


@pytest.mark.asyncio
async def test_digest_does_not_fire_outside_window(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, orgs = await _seed(session_factory)
    t = await tickets.create(_ticket())
    await tickets.add_org(t.id, "acme")  # type: ignore[arg-type]
    job = _job(_view(tickets, orgs), fake_slack, session_factory)

    assert await job.execute(now_utc=_FRIDAY_BEFORE_UTC) == 0
    assert await job.execute(now_utc=_THURSDAY_WINDOW_UTC) == 0
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_digest_dms_each_csm_only_their_tickets(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, orgs = await _seed(session_factory)
    t_a = await tickets.create(_ticket(title="acme issue"))
    t_b = await tickets.create(_ticket(title="initech issue"))
    await tickets.add_org(t_a.id, "acme")  # type: ignore[arg-type]
    await tickets.add_org(t_b.id, "initech")  # type: ignore[arg-type]
    job = _job(_view(tickets, orgs), fake_slack, session_factory)

    sent = await job.execute(now_utc=_FRIDAY_WINDOW_UTC)

    assert sent == 2
    by_user = {uid: blocks for uid, blocks, _ in fake_slack.dm_blocks_sent}
    assert set(by_user) == {_CSM_A, _CSM_B}
    # CSM A's DM mentions Acme but never Initech.
    a_text = str(by_user[_CSM_A])
    assert "acme issue" in a_text and "initech issue" not in a_text


@pytest.mark.asyncio
async def test_digest_fires_once_per_iso_week(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, orgs = await _seed(session_factory)
    t = await tickets.create(_ticket())
    await tickets.add_org(t.id, "acme")  # type: ignore[arg-type]
    job = _job(_view(tickets, orgs), fake_slack, session_factory)

    assert await job.execute(now_utc=_FRIDAY_WINDOW_UTC) == 1
    # Same window, a tick later — bookmark suppresses the resend.
    assert await job.execute(now_utc=datetime(2026, 6, 26, 12, 45)) == 0
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
async def test_digest_burns_week_even_with_no_csm_tickets(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets, orgs = await _seed(session_factory)  # no tickets created
    job = _job(_view(tickets, orgs), fake_slack, session_factory)

    assert await job.execute(now_utc=_FRIDAY_WINDOW_UTC) == 0
    assert fake_slack.dm_blocks_sent == []
    # Bookmark was still set, so a re-tick stays silent.
    state = await SQLiteCSMDigestStateRepository(session_factory).get()
    assert state.last_fired_at is not None


# --- render_csm_tickets_blocks ----------------------------------------------


def test_render_empty_state() -> None:
    blocks = render_csm_tickets_blocks([], workspace_url="https://x.slack.com", scheduled=False)
    assert len(blocks) == 1
    assert "nothing open" in blocks[0]["text"]["text"]


def test_render_groups_by_priority() -> None:
    p1 = (_ticket(title="urgent", priority=Priority.P1), ["Acme"])
    p3 = (_ticket(title="minor", priority=Priority.P3), ["Acme"])
    blocks = render_csm_tickets_blocks(
        [p3, p1], workspace_url="https://x.slack.com", scheduled=True
    )
    rendered = str(blocks)
    assert "P1" in rendered and "P3" in rendered
    assert "urgent" in rendered and "minor" in rendered
