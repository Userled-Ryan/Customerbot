"""Integration tests for the Set-deadline ticket-card button."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.ticket_card import ACTION_SET_DEADLINE, build_blocks
from customerbot.application.tracking.set_deadline import (
    OpenSetDeadlineModal,
    SubmitDeadline,
)
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _bug(*, deadline: date | None = None) -> Ticket:
    return Ticket(
        title="checkout broken",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=TicketStatus.IN_PROGRESS,
        lane=Lane.SE_ACTION,
        priority=Priority.P2,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="users hang on submit",
        card_channel_id="C_SE_TICKETS",
        card_message_ts="1700000000.000100",
        deadline=deadline,
        created_at=datetime(2026, 6, 1, 9, 0),
    )


def _view_builder(*, ticket_id: int, current_deadline: date | None) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": "set_deadline",
        "private_metadata": str(ticket_id),
        "current_deadline_initial": current_deadline.isoformat() if current_deadline else None,
        "blocks": [],
    }


# --- Card rendering ---------------------------------------------------------


def test_card_renders_set_deadline_when_unset() -> None:
    ticket = _bug(deadline=None)
    ticket.id = 7
    blocks = build_blocks(ticket, [])
    fields_block = next(b for b in blocks if b.get("type") == "section" and "fields" in b)
    field_texts = [f["text"] for f in fields_block["fields"]]
    assert any(t == "*Deadline*\n—" for t in field_texts)
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    secondary = action_blocks[1]["elements"]
    set_deadline_btn = next(el for el in secondary if el["action_id"] == ACTION_SET_DEADLINE)
    # Unset → label says "Set deadline"; once set the label flips to "Change deadline".
    assert set_deadline_btn["text"]["text"] == "Set deadline"
    assert set_deadline_btn["value"] == "7"


def test_card_renders_change_deadline_when_set() -> None:
    ticket = _bug(deadline=date(2026, 6, 15))
    ticket.id = 7
    blocks = build_blocks(ticket, [])
    fields_block = next(b for b in blocks if b.get("type") == "section" and "fields" in b)
    field_texts = [f["text"] for f in fields_block["fields"]]
    assert any("Jun 2026" in t for t in field_texts)
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    set_deadline_btn = next(
        el for el in action_blocks[1]["elements"] if el["action_id"] == ACTION_SET_DEADLINE
    )
    assert set_deadline_btn["text"]["text"] == "Change deadline"


# --- OpenSetDeadlineModal ----------------------------------------------------


@pytest.mark.asyncio
async def test_open_modal_prefills_with_current_deadline(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    created = await tickets.create(_bug(deadline=date(2026, 6, 15)))
    assert created.id is not None

    use_case = OpenSetDeadlineModal(slack=fake_slack, tickets=tickets, view_builder=_view_builder)
    await use_case.execute(trigger_id="T1", ticket_id=created.id)
    assert len(fake_slack.views_opened) == 1
    _trigger, view = fake_slack.views_opened[0]
    assert view["private_metadata"] == str(created.id)
    assert view["current_deadline_initial"] == "2026-06-15"


# --- SubmitDeadline ----------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_deadline_updates_and_refreshes_card(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug())
    assert created.id is not None

    use_case = SubmitDeadline(slack=fake_slack, tickets=tickets, orgs=orgs)
    refreshed = await use_case.execute(
        ticket_id=created.id, deadline=date(2026, 6, 30), by_user_id="U_SE"
    )
    assert refreshed is not None
    assert refreshed.deadline == date(2026, 6, 30)
    # Card refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)


@pytest.mark.asyncio
async def test_submit_deadline_with_none_clears_existing(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_bug(deadline=date(2026, 6, 15)))
    assert created.id is not None

    use_case = SubmitDeadline(slack=fake_slack, tickets=tickets, orgs=orgs)
    refreshed = await use_case.execute(ticket_id=created.id, deadline=None, by_user_id="U_SE")
    assert refreshed is not None
    assert refreshed.deadline is None
    # Card refreshed once to reflect the clear.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)


@pytest.mark.asyncio
async def test_submit_deadline_noop_when_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    target = date(2026, 6, 15)
    created = await tickets.create(_bug(deadline=target))
    assert created.id is not None

    use_case = SubmitDeadline(slack=fake_slack, tickets=tickets, orgs=orgs)
    refreshed = await use_case.execute(ticket_id=created.id, deadline=target, by_user_id="U_SE")
    assert refreshed is not None
    assert refreshed.deadline == target
    # No card refresh when nothing changed.
    assert fake_slack.messages_updated == []


@pytest.mark.asyncio
async def test_submit_deadline_on_missing_ticket_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    use_case = SubmitDeadline(slack=fake_slack, tickets=tickets, orgs=orgs)
    assert (
        await use_case.execute(ticket_id=999, deadline=date(2026, 6, 15), by_user_id="U_SE")
    ) is None
    assert fake_slack.messages_updated == []
