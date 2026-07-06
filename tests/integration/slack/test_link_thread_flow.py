"""Manual "Link to existing ticket" flow, against real SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.link_thread import OpenLinkModal, SubmitLinkThread
from customerbot.application.intake.support_threads import IN_FLIGHT_REACTION
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.slack.modals import link_ticket
from tests.conftest import FakeSlackPort

SUPPORT_CHANNEL = "C_SUPPORT"


async def _seed_ticket(
    factory: async_sessionmaker[AsyncSession], title: str, org_id: str | None = None
) -> Ticket:
    tickets = SQLiteTicketRepository(factory)
    t = await tickets.create(
        Ticket(
            title=title,
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
        )
    )
    if org_id is not None:
        assert t.id is not None
        await SQLiteOrgRepository(factory).upsert(Org(id=org_id, name=org_id.upper()))
        await tickets.add_org(t.id, org_id)
    return t


def _open_modal(factory: async_sessionmaker[AsyncSession], slack: FakeSlackPort) -> OpenLinkModal:
    return OpenLinkModal(
        slack=slack,
        tickets=SQLiteTicketRepository(factory),
        orgs=SQLiteOrgRepository(factory),
        view_builder=link_ticket.build_view,
        support_channel_id=SUPPORT_CHANNEL,
    )


@pytest.mark.asyncio
async def test_open_modal_lists_live_tickets_with_org(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    t = await _seed_ticket(session_factory, "Publishing broken", org_id="acme")
    assert t.id is not None

    await _open_modal(session_factory, fake_slack).execute(
        trigger_id="T1", channel_id=SUPPORT_CHANNEL, thread_ts="100.1", invoker_user_id="U_SE"
    )

    assert len(fake_slack.views_opened) == 1
    _trigger, view = fake_slack.views_opened[0]
    option = view["blocks"][0]["element"]["options"][0]
    assert option["value"] == str(t.id)
    assert t.display_id in option["text"]["text"]
    assert "ACME" in option["text"]["text"]


@pytest.mark.asyncio
async def test_open_modal_outside_support_channel_is_ephemeral(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_ticket(session_factory, "Publishing broken")

    view_id = await _open_modal(session_factory, fake_slack).execute(
        trigger_id="T1", channel_id="C_OTHER", thread_ts="100.1", invoker_user_id="U_SE"
    )

    assert view_id is None
    assert fake_slack.views_opened == []
    assert len(fake_slack.ephemerals_sent) == 1
    assert fake_slack.ephemerals_sent[0][0] == "C_OTHER"


@pytest.mark.asyncio
async def test_open_modal_warns_when_thread_already_linked(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    from datetime import UTC, datetime

    tickets = SQLiteTicketRepository(session_factory)
    other = await _seed_ticket(session_factory, "Existing ticket", org_id="acme")
    await _seed_ticket(session_factory, "Another ticket")
    assert other.id is not None
    await tickets.link_support_thread(
        other.id,
        SUPPORT_CHANNEL,
        "100.1",
        by_user_id="U_SE",
        now=datetime.now(UTC).replace(tzinfo=None),
    )

    await _open_modal(session_factory, fake_slack).execute(
        trigger_id="T1", channel_id=SUPPORT_CHANNEL, thread_ts="100.1", invoker_user_id="U_SE"
    )

    _trigger, view = fake_slack.views_opened[0]
    note = view["blocks"][0]["text"]["text"]
    assert other.display_id in note
    assert "move" in note.lower()


@pytest.mark.asyncio
async def test_submit_link_attaches_and_reacts(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    t = await _seed_ticket(session_factory, "Publishing broken")
    assert t.id is not None

    submit = SubmitLinkThread(slack=fake_slack, tickets=tickets)
    await submit.execute(
        channel_id=SUPPORT_CHANNEL, thread_ts="100.1", target_ticket_id=t.id, by_user_id="U_SE"
    )

    assert await tickets.list_support_threads(t.id) == [(SUPPORT_CHANNEL, "100.1")]
    assert fake_slack.reactions_added == [(SUPPORT_CHANNEL, "100.1", IN_FLIGHT_REACTION)]
    assert len(fake_slack.ephemerals_sent) == 1


@pytest.mark.asyncio
async def test_submit_link_moves_from_other_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    from datetime import UTC, datetime

    tickets = SQLiteTicketRepository(session_factory)
    first = await _seed_ticket(session_factory, "First")
    second = await _seed_ticket(session_factory, "Second")
    assert first.id is not None and second.id is not None
    await tickets.link_support_thread(
        first.id,
        SUPPORT_CHANNEL,
        "100.1",
        by_user_id="U_SE",
        now=datetime.now(UTC).replace(tzinfo=None),
    )

    submit = SubmitLinkThread(slack=fake_slack, tickets=tickets)
    await submit.execute(
        channel_id=SUPPORT_CHANNEL, thread_ts="100.1", target_ticket_id=second.id, by_user_id="U_SE"
    )

    assert await tickets.list_support_threads(first.id) == []
    assert await tickets.list_support_threads(second.id) == [(SUPPORT_CHANNEL, "100.1")]
