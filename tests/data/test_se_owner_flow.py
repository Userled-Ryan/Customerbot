from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.apply_se_owner import ApplySeOwnerChange
from customerbot.application.intake.se_owner_actions import SeOwnerChangePayload
from customerbot.application.linear.sync import LinearSync
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeLinearPort, FakeSlackPort


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[SQLiteTicketRepository, SQLiteOrgRepository, Ticket]:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    created = await tickets.create(
        Ticket(
            title="Publishing fails",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            lane=Lane.SE_ACTION,
            reporter_user_id="U_SE",
            se_owner_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            description="",
            card_channel_id="C_SE_TICKETS",
            card_message_ts="1700000000.000100",
        )
    )
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    return tickets, orgs, created


@pytest.mark.asyncio
async def test_apply_se_owner_updates_db_card_and_linear(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed(session_factory)
    # Give the mirror an issue up front so sync_owner assigns it.
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)

    apply = ApplySeOwnerChange(tickets=tickets, slack=fake_slack, orgs=orgs, linear=sync)
    result = await apply.execute(
        SeOwnerChangePayload(ticket_id=created.id or 0, owner_user_id="U_ELIZA"),
        by_user_id="U_SE",
    )

    assert result == "U_ELIZA"
    # DB updated.
    persisted = await tickets.get(created.id or 0)
    assert persisted is not None and persisted.se_owner_user_id == "U_ELIZA"
    # Card refreshed (chat.update on the stored card message).
    assert fake_slack.messages_updated
    # Linear assignee mirrored to the new owner.
    assert ("lin_1", "U_ELIZA") in fake_linear.assignments


@pytest.mark.asyncio
async def test_apply_se_owner_noop_when_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
    fake_linear: FakeLinearPort,
) -> None:
    tickets, orgs, created = await _seed(session_factory)
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    apply = ApplySeOwnerChange(tickets=tickets, slack=fake_slack, orgs=orgs, linear=sync)

    # Re-select the already-current owner — nothing changes, no card refresh.
    result = await apply.execute(
        SeOwnerChangePayload(ticket_id=created.id or 0, owner_user_id="U_SE"),
        by_user_id="U_SE",
    )

    assert result == "U_SE"
    assert fake_slack.messages_updated == []
    assert fake_linear.assignments == []
