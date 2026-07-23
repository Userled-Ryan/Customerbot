"""Integration tests for the Set-stakeholder ticket-card button.

The stakeholder is the affected org's CSM, so "set stakeholder" writes to
`orgs.csm_user_id` — the change sticks across every ticket touching that org.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.ticket_card import ACTION_SET_STAKEHOLDER, build_blocks
from customerbot.application.tracking.set_stakeholder import (
    OpenSetStakeholderModal,
    SubmitSetStakeholder,
)
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
from customerbot.integration.slack.modals import set_stakeholder
from customerbot.integration.slack.modals.submission_payload import parse_set_stakeholder
from tests.conftest import FakeSlackPort


def _bug() -> Ticket:
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
        created_at=datetime(2026, 6, 1, 9, 0),
    )


# --- Card rendering ---------------------------------------------------------


def test_card_renders_set_stakeholder_button() -> None:
    ticket = _bug()
    ticket.id = 7
    blocks = build_blocks(ticket, ["Acme"], ["U_CSM"])
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    secondary = action_blocks[1]["elements"]
    btn = next(el for el in secondary if el["action_id"] == ACTION_SET_STAKEHOLDER)
    assert btn["text"]["text"] == "Set stakeholder"
    assert btn["value"] == "7"


# --- Modal builder ----------------------------------------------------------


def test_build_view_prefills_one_picker_per_org() -> None:
    view = set_stakeholder.build_view(
        ticket_id=7,
        orgs=[("acme", "Acme", "U_CSM1"), ("globex", "Globex", None)],
    )
    assert view["private_metadata"] == "7"
    input_blocks = [b for b in view["blocks"] if b.get("type") == "input"]
    assert len(input_blocks) == 2
    acme = next(b for b in input_blocks if b["block_id"] == set_stakeholder.block_id_for("acme"))
    assert acme["element"]["initial_user"] == "U_CSM1"
    globex = next(
        b for b in input_blocks if b["block_id"] == set_stakeholder.block_id_for("globex")
    )
    # No current CSM → no initial_user is pre-filled.
    assert "initial_user" not in globex["element"]


def test_build_view_with_no_orgs_shows_info() -> None:
    view = set_stakeholder.build_view(ticket_id=7, orgs=[])
    assert "submit" not in view  # nothing to save
    assert not any(b.get("type") == "input" for b in view["blocks"])


# --- Parser -----------------------------------------------------------------


def test_parse_set_stakeholder_reads_selected_and_cleared() -> None:
    view = {
        "private_metadata": "7",
        "state": {
            "values": {
                set_stakeholder.block_id_for("acme"): {
                    set_stakeholder.action_id_for("acme"): {"selected_user": "U_NEW"},
                },
                set_stakeholder.block_id_for("globex"): {
                    set_stakeholder.action_id_for("globex"): {"selected_user": None},
                },
            }
        },
    }
    ticket_id, assignments = parse_set_stakeholder(view)
    assert ticket_id == 7
    assert assignments == {"acme": "U_NEW", "globex": None}


# --- OpenSetStakeholderModal -------------------------------------------------


@pytest.mark.asyncio
async def test_open_modal_lists_orgs_with_current_csm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM1"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = OpenSetStakeholderModal(
        slack=fake_slack,
        tickets=tickets,
        orgs=orgs,
        view_builder=set_stakeholder.build_view,
    )
    await use_case.execute(trigger_id="T1", ticket_id=created.id)
    assert len(fake_slack.views_opened) == 1
    _trigger, view = fake_slack.views_opened[0]
    assert view["private_metadata"] == str(created.id)
    acme = next(
        b for b in view["blocks"] if b.get("block_id") == set_stakeholder.block_id_for("acme")
    )
    assert acme["element"]["initial_user"] == "U_CSM1"


# --- SubmitSetStakeholder ----------------------------------------------------


@pytest.mark.asyncio
async def test_submit_updates_org_csm_and_refreshes_card(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_OLD"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = SubmitSetStakeholder(slack=fake_slack, tickets=tickets, orgs=orgs)
    changed = await use_case.execute(
        ticket_id=created.id, assignments={"acme": "U_NEW"}, by_user_id="U_SE"
    )
    assert changed is True
    # Persisted on the org — so it sticks for every other ticket too.
    refreshed_org = await orgs.get("acme")
    assert refreshed_org is not None
    assert refreshed_org.csm_user_id == "U_NEW"
    # Card refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)


@pytest.mark.asyncio
async def test_submit_clears_csm_when_none(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_OLD"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = SubmitSetStakeholder(slack=fake_slack, tickets=tickets, orgs=orgs)
    changed = await use_case.execute(
        ticket_id=created.id, assignments={"acme": None}, by_user_id="U_SE"
    )
    assert changed is True
    refreshed_org = await orgs.get("acme")
    assert refreshed_org is not None
    assert refreshed_org.csm_user_id is None


@pytest.mark.asyncio
async def test_submit_noop_when_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = SubmitSetStakeholder(slack=fake_slack, tickets=tickets, orgs=orgs)
    changed = await use_case.execute(
        ticket_id=created.id, assignments={"acme": "U_CSM"}, by_user_id="U_SE"
    )
    assert changed is False
    assert fake_slack.messages_updated == []


@pytest.mark.asyncio
async def test_submit_ignores_orgs_not_on_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_OLD"))
    await orgs.upsert(Org(id="globex", name="Globex", csm_user_id="U_OTHER"))
    created = await tickets.create(_bug())
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = SubmitSetStakeholder(slack=fake_slack, tickets=tickets, orgs=orgs)
    # `globex` isn't linked to this ticket — it must not be touched.
    changed = await use_case.execute(
        ticket_id=created.id, assignments={"globex": "U_NEW"}, by_user_id="U_SE"
    )
    assert changed is False
    globex = await orgs.get("globex")
    assert globex is not None
    assert globex.csm_user_id == "U_OTHER"


@pytest.mark.asyncio
async def test_submit_on_missing_ticket_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    use_case = SubmitSetStakeholder(slack=fake_slack, tickets=tickets, orgs=orgs)
    changed = await use_case.execute(
        ticket_id=999, assignments={"acme": "U_NEW"}, by_user_id="U_SE"
    )
    assert changed is False
    assert fake_slack.messages_updated == []
