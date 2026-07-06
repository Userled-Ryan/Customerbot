"""Integration tests for Chunk 10 — reclassify auto-notify.

Covers:
- `SubmitReclassify` updates the ticket's type/subtype, writes an
  `event_reclassifications` row, refreshes the card, then immediately
  notifies internal stakeholders (a DM per user, a post per channel) and
  logs one `event_comms_log` row per recipient.
- Recipient resolution: original reporter + new owner + CSM-of-affected-orgs
  + `@support` channel when lane is Dev Action. Customers are never included.
- No-op when neither type nor subtype changed.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.submissions import ReclassifySubmission
from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.reclassify import SubmitReclassify
from customerbot.data.database import EventCommsLogRow, EventReclassificationRow
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
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
from tests.conftest import FakeLinearPort, FakeSlackPort


def _ts(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute)


def _bug(
    *,
    ticket_type: TicketType = TicketType.BUG,
    subtype: TicketSubtype = TicketSubtype.PLATFORM_WIDE,
    lane: Lane | None = Lane.SE_ACTION,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    reporter: str = "U_REPORTER",
    card_channel_id: str | None = "C_SE_TICKETS",
    card_message_ts: str | None = "1700000000.000100",
) -> Ticket:
    return Ticket(
        title="checkout broken on safari",
        type=ticket_type,
        subtype=subtype,
        status=status,
        lane=lane,
        priority=Priority.P2,
        reporter_user_id=reporter,
        source=Source.CUSTOMER_CHANNEL,
        original_slack_link="https://test.slack.com/archives/C/p123",
        description="step-by-step repro",
        card_channel_id=card_channel_id,
        card_message_ts=card_message_ts,
        created_at=_ts(2026, 6, 1),
    )


def _submit(
    tickets: SQLiteTicketRepository,
    events: SQLiteEventLogRepository,
    orgs: SQLiteOrgRepository,
    fake_slack: FakeSlackPort,
    *,
    support_handle: str | None = None,
    support_ping_channel_id: str | None = None,
    linear: LinearSync | None = None,
) -> SubmitReclassify:
    return SubmitReclassify(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        support_handle=support_handle,
        support_ping_channel_id=support_ping_channel_id,
        linear=linear,
    )


# --- SubmitReclassify --------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_reclassify_updates_ticket_and_writes_event(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None

    use_case = _submit(
        tickets, events, orgs, fake_slack,
        support_handle="S0123ABCD",
        support_ping_channel_id="C_SUPPORT",
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.CONFIG,
        new_subtype=TicketSubtype.SETUP_INTEGRATION,
        reason="customer's webhook misconfigured — not a platform bug",
        next_step="walk through the webhook setup tomorrow",
        owner_user_id="U_OWNER",
    )
    sent = await use_case.execute(submission, by_user_id="U_SE")
    # Reporter + owner (no CSMs, lane not dev so no support channel).
    assert sent == 2

    # Ticket row updated.
    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    assert refreshed.type == TicketType.CONFIG
    assert refreshed.subtype == TicketSubtype.SETUP_INTEGRATION

    # event_reclassifications row written.
    async with session_factory() as session:
        reclass_rows = list((await session.execute(select(EventReclassificationRow))).scalars())
    assert len(reclass_rows) == 1
    row = reclass_rows[0]
    assert row.from_type == TicketType.BUG.value
    assert row.to_type == TicketType.CONFIG.value
    assert row.from_subtype == TicketSubtype.PLATFORM_WIDE.value
    assert row.to_subtype == TicketSubtype.SETUP_INTEGRATION.value
    assert row.reason == submission.reason
    assert row.next_step == submission.next_step
    assert row.owner_user_id == "U_OWNER"

    # Card refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)

    # Notice DM'd straight to the stakeholders — no draft, no Send/Cancel buttons.
    assert {u for u, _, _ in fake_slack.dm_blocks_sent} == {"U_REPORTER", "U_OWNER"}
    for _, blocks, _ in fake_slack.dm_blocks_sent:
        assert not any(b.get("type") == "actions" for b in blocks)
        body = blocks[1]["text"]["text"]
        assert "Bug → Config" in body


@pytest.mark.asyncio
async def test_submit_reclassify_to_feature_request_uses_friendly_label(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None

    use_case = _submit(tickets, events, orgs, fake_slack)
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.FEATURE_REQUEST,
        new_subtype=TicketSubtype.NEW_CAPABILITY,
        reason="this is net-new functionality, not a defect",
        next_step="pass this to our product team",
        owner_user_id="U_OWNER",
    )
    await use_case.execute(submission, by_user_id="U_SE")

    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    assert refreshed.type == TicketType.FEATURE_REQUEST
    assert refreshed.subtype == TicketSubtype.NEW_CAPABILITY

    body = fake_slack.dm_blocks_sent[0][1][1]["text"]["text"]
    assert "Bug → Product change" in body
    assert "platform-wide → new-capability" in body


@pytest.mark.asyncio
async def test_submit_reclassify_swaps_linear_type_label(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
    fake_linear: FakeLinearPort,
) -> None:
    """A type change (Bug → Config) swaps the Linear type label so reports stay
    filterable; the org labels are left untouched."""
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None
    # Pre-attach a Linear mirror so the swap targets an existing issue.
    await tickets.set_linear_issue(
        created.id, issue_id="lin_9", identifier="PRD-9", url="https://linear.app/x/PRD-9"
    )

    use_case = _submit(
        tickets, events, orgs, fake_slack,
        linear=LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs),
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.CONFIG,
        new_subtype=TicketSubtype.SETUP_INTEGRATION,
        reason="config, not a bug",
        next_step="set it up",
        owner_user_id="U_OWNER",
    )
    await use_case.execute(submission, by_user_id="U_SE")

    assert fake_linear.label_removes == [("lin_9", "typelabel_bug")]
    assert fake_linear.label_adds == [("lin_9", "typelabel_config")]


@pytest.mark.asyncio
async def test_submit_reclassify_is_noop_when_type_and_subtype_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None

    use_case = _submit(tickets, events, orgs, fake_slack)
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=created.type,
        new_subtype=created.subtype,
        reason="no-op",
        next_step="no-op",
        owner_user_id="U_OWNER",
    )
    sent = await use_case.execute(submission, by_user_id="U_SE")
    assert sent == 0
    # No event row, no card refresh, no DM.
    async with session_factory() as session:
        rows = list((await session.execute(select(EventReclassificationRow))).scalars())
    assert rows == []
    assert fake_slack.messages_updated == []
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_submit_reclassify_on_missing_ticket_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    use_case = _submit(tickets, events, orgs, fake_slack)
    sent = await use_case.execute(
        ReclassifySubmission(
            ticket_id=999,
            new_type=TicketType.CONFIG,
            new_subtype=TicketSubtype.SETUP_INTEGRATION,
            reason="r",
            next_step="n",
            owner_user_id="U_OWNER",
        ),
        by_user_id="U_SE",
    )
    assert sent == 0
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_submit_reclassify_notifies_full_recipient_set_and_logs_comms(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM_A"))
    await orgs.upsert(Org(id="globex", name="Globex", csm_user_id="U_CSM_B"))
    # Already in Dev Action lane → @support channel is "involved".
    created = await tickets.create(_bug(lane=Lane.DEV_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    await tickets.add_org(created.id, "globex")

    use_case = _submit(
        tickets, events, orgs, fake_slack,
        support_handle="S0123ABCD",
        support_ping_channel_id="C_SUPPORT",
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.FAQ,
        new_subtype=TicketSubtype.EXISTING_ARTICLE,
        reason="we have a docs page covering this",
        next_step="send the article link",
        owner_user_id="U_OWNER",
    )
    sent = await use_case.execute(submission, by_user_id="U_SE")
    # Reporter + owner + both CSMs (4 DMs) + support channel (1 post).
    assert sent == 5

    dm_users = {u for u, _, _ in fake_slack.dm_blocks_sent}
    assert dm_users == {"U_REPORTER", "U_OWNER", "U_CSM_A", "U_CSM_B"}
    posted_channels = [ch for ch, _, _ in fake_slack.blocks_posted]
    assert posted_channels == ["C_SUPPORT"]
    # The support post carries the group mention.
    support_body = fake_slack.blocks_posted[0][1][1]["text"]["text"]
    assert "<!subteam^S0123ABCD>" in support_body

    # One comms-log row per recipient.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    reclass_rows = [c for c in comms if c.note == "reclassify-notice"]
    assert len(reclass_rows) == 5
    channels = {c.channel for c in reclass_rows}
    assert channels == {"dm:U_REPORTER", "dm:U_OWNER", "dm:U_CSM_A", "dm:U_CSM_B", "C_SUPPORT"}


@pytest.mark.asyncio
async def test_submit_reclassify_dedupes_when_reporter_equals_owner(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    created = await tickets.create(_bug(reporter="U_SAME"))
    assert created.id is not None

    use_case = _submit(tickets, events, orgs, fake_slack)
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.CONFIG,
        new_subtype=TicketSubtype.REPORTING,
        reason="r",
        next_step="n",
        owner_user_id="U_SAME",
    )
    sent = await use_case.execute(submission, by_user_id="U_SE")
    assert sent == 1
    assert [u for u, _, _ in fake_slack.dm_blocks_sent] == ["U_SAME"]


@pytest.mark.asyncio
async def test_submit_reclassify_excludes_support_when_lane_not_dev(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    # SE_ACTION lane — support hasn't been brought in yet.
    created = await tickets.create(_bug(lane=Lane.SE_ACTION))
    assert created.id is not None

    use_case = _submit(
        tickets, events, orgs, fake_slack,
        support_handle="S0123ABCD",
        support_ping_channel_id="C_SUPPORT",
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.CONFIG,
        new_subtype=TicketSubtype.REPORTING,
        reason="r",
        next_step="n",
        owner_user_id="U_OWNER",
    )
    await use_case.execute(submission, by_user_id="U_SE")
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_submit_reclassify_never_posts_to_customer_channel(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)

    # The customer channel where the ticket originated.
    customer_channel = "C_CUSTOMER_PRIVATE"
    await orgs.upsert(
        Org(id="acme", name="Acme", csm_user_id="U_CSM", slack_channel_id=customer_channel)
    )
    created = await tickets.create(_bug(lane=Lane.DEV_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    use_case = _submit(
        tickets, events, orgs, fake_slack,
        support_ping_channel_id="C_SUPPORT",
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.CONFIG,
        new_subtype=TicketSubtype.REPORTING,
        reason="r",
        next_step="n",
        owner_user_id="U_OWNER",
    )
    await use_case.execute(submission, by_user_id="U_SE")

    # Verify the customer channel is nowhere in posted blocks or DM recipients.
    for ch, _, _ in fake_slack.blocks_posted:
        assert ch != customer_channel
    for user, _, _ in fake_slack.dm_blocks_sent:
        assert user != customer_channel
    # And no comms-log row points at it.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    assert all(customer_channel not in c.channel for c in comms)
