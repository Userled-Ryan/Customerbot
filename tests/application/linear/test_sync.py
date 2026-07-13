from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.linear.sync import LinearSync
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeLinearPort


def _bug() -> Ticket:
    return Ticket(
        title="Publishing fails on Safari",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        severity=Severity.BLOCKING,
        lane=Lane.SE_ACTION,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="Crashes on iOS 18.",
    )


async def _seed_ticket_with_org(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[SQLiteTicketRepository, SQLiteOrgRepository, Ticket]:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    return tickets, orgs, created


@pytest.mark.asyncio
async def test_mirror_new_ticket_creates_persists_and_labels(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)

    await sync.mirror_new_ticket(created)

    # One issue created, with the per-type + per-org labels attached (type first).
    assert len(fake_linear.created_issues) == 1
    issue = fake_linear.created_issues[0]
    assert issue["state"] == LinearWorkflowState.TRIAGE
    assert issue["label_ids"] == ["typelabel_bug", "label_acme"]
    assert issue["in_project"] is False

    # Ref persisted onto the ticket.
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.linear_issue_id == "lin_1"
    assert refreshed.linear_issue_identifier == "PRD-1"


@pytest.mark.asyncio
async def test_mirror_new_config_ticket_attaches_config_type_label(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    """Config tickets carry a `Config` type label so Linear reports can filter
    them out from Bug tickets."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    created = await tickets.create(
        Ticket(
            title="Enable LinkedIn ads behind feature flag",
            type=TicketType.CONFIG,
            subtype=TicketSubtype.SETUP_INTEGRATION,
            lane=Lane.SE_ACTION,
            reporter_user_id="U_SE",
            source=Source.CALL,
            description="",
        )
    )
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)

    await sync.mirror_new_ticket(created)

    issue = fake_linear.created_issues[0]
    assert issue["label_ids"] == ["typelabel_config", "label_acme"]
    assert fake_linear.type_labels == {"config": "typelabel_config"}


@pytest.mark.asyncio
async def test_mirror_new_ticket_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)

    await sync.mirror_new_ticket(created)
    # Re-load with the issue id set, then call again — must not create a second.
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    await sync.mirror_new_ticket(refreshed)

    assert len(fake_linear.created_issues) == 1


@pytest.mark.asyncio
async def test_sync_type_label_swaps_old_for_new(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)

    await sync.sync_type_label(created.id or 0, from_type=TicketType.BUG, to_type=TicketType.CONFIG)

    assert fake_linear.label_removes == [("lin_1", "typelabel_bug")]
    assert fake_linear.label_adds == [("lin_1", "typelabel_config")]


@pytest.mark.asyncio
async def test_sync_type_label_is_noop_when_type_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)

    await sync.sync_type_label(created.id or 0, from_type=TicketType.BUG, to_type=TicketType.BUG)

    assert fake_linear.label_removes == []
    assert fake_linear.label_adds == []


@pytest.mark.asyncio
async def test_mark_done_silently_creates_then_closes_when_missing(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)

    # No mirror yet — should create one, then immediately cancel it.
    await sync.mark_done_silently(created.id or 0, state=LinearWorkflowState.CANCELED)

    assert len(fake_linear.created_issues) == 1
    assert fake_linear.state_updates == [("lin_1", LinearWorkflowState.CANCELED)]


@pytest.mark.asyncio
async def test_ensure_open_for_dev_sets_in_progress_and_adds_to_project(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)

    await sync.ensure_open_for_dev(created.id or 0)

    assert ("lin_1", LinearWorkflowState.IN_PROGRESS) in fake_linear.state_updates
    assert "lin_1" in fake_linear.project_adds


@pytest.mark.asyncio
async def test_se_lane_ticket_lands_in_se_project(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    """A freshly-created (SE-lane) ticket is mirrored into the SE Responder
    project, not the dev Product Responder project."""
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)

    await sync.mirror_new_ticket(created)

    issue = fake_linear.created_issues[0]
    assert issue["in_se_project"] is True
    assert issue["in_project"] is False
    assert "lin_1" in fake_linear.se_project_adds


@pytest.mark.asyncio
async def test_dev_lane_ticket_lands_in_dev_project(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    bug = _bug()
    bug.lane = Lane.DEV_ACTION
    created = await tickets.create(bug)
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)

    await sync.mirror_new_ticket(created)

    issue = fake_linear.created_issues[0]
    assert issue["in_project"] is True
    assert issue["in_se_project"] is False


@pytest.mark.asyncio
async def test_ensure_in_se_project_moves_issue_back(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    """Return-to-SE pulls the mirror back into the SE Responder project."""
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)

    await sync.ensure_in_se_project(created.id or 0)

    # Once from create (SE-lane), once from the explicit re-add.
    assert fake_linear.se_project_adds.count("lin_1") == 2


@pytest.mark.asyncio
async def test_create_and_sync_owner_mirror_assignee(
    session_factory: async_sessionmaker[AsyncSession],
    fake_linear: FakeLinearPort,
) -> None:
    """The SE owner is mirrored as the Linear assignee on create and on change."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    bug = _bug()
    bug.se_owner_user_id = "U_SE"
    created = await tickets.create(bug)
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)

    await sync.mirror_new_ticket(created)
    assert fake_linear.created_issues[0]["assignee_slack_id"] == "U_SE"

    # Reassign, then sync_owner pushes the new owner onto the issue.
    from datetime import UTC, datetime

    await tickets.update_se_owner(created.id, "U_OTHER", now=datetime.now(UTC).replace(tzinfo=None))
    await sync.sync_owner(created.id)
    assert ("lin_1", "U_OTHER") in fake_linear.assignments


@pytest.mark.asyncio
async def test_failure_isolation_swallows_linear_errors(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets, orgs, created = await _seed_ticket_with_org(session_factory)
    boom = FakeLinearPort(raise_on_create=True)
    sync = LinearSync(linear=boom, tickets=tickets, orgs=orgs)

    # Must not raise — Linear is best-effort.
    await sync.mirror_new_ticket(created)

    # Ticket is untouched: no mirror persisted.
    refreshed = await tickets.get(created.id or 0)
    assert refreshed is not None
    assert refreshed.linear_issue_id is None
