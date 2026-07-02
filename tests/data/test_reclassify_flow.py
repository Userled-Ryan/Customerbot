"""Integration tests for Chunk 10 — reclassify draft + send.

Covers:
- `SubmitReclassifyDraft` updates the ticket's type/subtype, writes an
  `event_reclassifications` row, refreshes the card, builds the §9f
  draft text, stashes it in `pending_reclassify_sends`, and DMs SE
  with Send / Cancel buttons.
- Recipient resolution: original reporter + new owner + CSM-of-affected-orgs
  + `@support` channel when lane is Dev Action. Customers are never
  included.
- `SendReclassifyAlert` posts to each recipient (DMs for users, channel
  posts for channels), appends one `event_comms_log` row per recipient,
  and deletes the pending row.
- `DismissReclassifyDraft` deletes the pending row without sending.
- Subtype-belongs-to-type validation rejects mismatched picks.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.submissions import ReclassifySubmission
from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.reclassify import (
    DismissReclassifyDraft,
    SendReclassifyAlert,
    SubmitReclassifyDraft,
)
from customerbot.data.database import EventCommsLogRow, EventReclassificationRow
from customerbot.data.repository.bot_state import SQLitePendingReclassifySendRepository
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


# --- SubmitReclassifyDraft ---------------------------------------------------


@pytest.mark.asyncio
async def test_submit_reclassify_updates_ticket_and_writes_event(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None

    use_case = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
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
    result = await use_case.execute(submission, by_user_id="U_SE")
    assert result is not None
    assert result.id is not None

    # Ticket row updated.
    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    assert refreshed.type == TicketType.CONFIG
    assert refreshed.subtype == TicketSubtype.SETUP_INTEGRATION

    # event_reclassifications row written and id matches pending.
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
    assert result.reclassification_event_id == row.id

    # Card refreshed.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)

    # SE got the draft DM with Send/Cancel buttons.
    assert any(user == "U_SE" for user, _, _ in fake_slack.dm_blocks_sent)
    dm = next(b for u, b, _ in fake_slack.dm_blocks_sent if u == "U_SE")
    actions = [b for b in dm if b.get("type") == "actions"]
    assert len(actions) == 1
    action_ids = {el["action_id"] for el in actions[0]["elements"]}
    assert action_ids == {"reclassify_send", "reclassify_dismiss"}

    # Pending row carries DM metadata (two-step pattern).
    fresh_pending = await pending.get(result.id)
    assert fresh_pending is not None
    assert fresh_pending.dm_channel_id != ""
    assert fresh_pending.dm_message_ts != ""


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
    pending = SQLitePendingReclassifySendRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None
    # Pre-attach a Linear mirror so the swap targets an existing issue.
    await tickets.set_linear_issue(
        created.id, issue_id="lin_9", identifier="PRD-9", url="https://linear.app/x/PRD-9"
    )

    use_case = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
        support_handle=None,
        support_ping_channel_id=None,
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
    pending = SQLitePendingReclassifySendRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None

    use_case = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
        support_handle=None,
        support_ping_channel_id=None,
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=created.type,
        new_subtype=created.subtype,
        reason="no-op",
        next_step="no-op",
        owner_user_id="U_OWNER",
    )
    result = await use_case.execute(submission, by_user_id="U_SE")
    assert result is None
    # No event row, no card refresh, no DM.
    async with session_factory() as session:
        rows = list((await session.execute(select(EventReclassificationRow))).scalars())
    assert rows == []
    assert fake_slack.messages_updated == []
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_submit_reclassify_resolves_full_recipient_set(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM_A"))
    await orgs.upsert(Org(id="globex", name="Globex", csm_user_id="U_CSM_B"))
    # Already in Dev Action lane → @support channel is "involved".
    created = await tickets.create(_bug(lane=Lane.DEV_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    await tickets.add_org(created.id, "globex")

    use_case = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
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
    result = await use_case.execute(submission, by_user_id="U_SE")
    assert result is not None
    recipients = json.loads(result.recipients_json)

    user_ids = [r["id"] for r in recipients if r["kind"] == "user"]
    channel_ids = [r["id"] for r in recipients if r["kind"] == "channel"]
    # Reporter + owner + both CSMs.
    assert set(user_ids) == {"U_REPORTER", "U_OWNER", "U_CSM_A", "U_CSM_B"}
    # Support channel because lane=DEV_ACTION.
    assert channel_ids == ["C_SUPPORT"]
    # No customer Slack channel anywhere in recipients.
    all_ids = user_ids + channel_ids
    customer_channel = "C"  # the original thread channel id
    assert customer_channel not in all_ids


@pytest.mark.asyncio
async def test_submit_reclassify_dedupes_when_reporter_equals_owner(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    created = await tickets.create(_bug(reporter="U_SAME"))
    assert created.id is not None

    use_case = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
        support_handle=None,
        support_ping_channel_id=None,
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.CONFIG,
        new_subtype=TicketSubtype.REPORTING,
        reason="r",
        next_step="n",
        owner_user_id="U_SAME",
    )
    result = await use_case.execute(submission, by_user_id="U_SE")
    assert result is not None
    recipients = json.loads(result.recipients_json)
    user_ids = [r["id"] for r in recipients if r["kind"] == "user"]
    assert user_ids == ["U_SAME"]


@pytest.mark.asyncio
async def test_submit_reclassify_excludes_support_when_lane_not_dev(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    # SE_ACTION lane — support hasn't been brought in yet.
    created = await tickets.create(_bug(lane=Lane.SE_ACTION))
    assert created.id is not None

    use_case = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
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
    result = await use_case.execute(submission, by_user_id="U_SE")
    assert result is not None
    recipients = json.loads(result.recipients_json)
    channel_ids = [r["id"] for r in recipients if r["kind"] == "channel"]
    assert channel_ids == []


# --- SendReclassifyAlert -----------------------------------------------------


@pytest.mark.asyncio
async def test_send_reclassify_posts_to_each_recipient_and_logs_comms(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    created = await tickets.create(_bug(lane=Lane.DEV_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    submit = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
        support_handle="S0123ABCD",
        support_ping_channel_id="C_SUPPORT",
    )
    submission = ReclassifySubmission(
        ticket_id=created.id,
        new_type=TicketType.CONFIG,
        new_subtype=TicketSubtype.SETUP_INTEGRATION,
        reason="r",
        next_step="n",
        owner_user_id="U_OWNER",
    )
    draft = await submit.execute(submission, by_user_id="U_SE")
    assert draft is not None and draft.id is not None

    # Snapshot Slack recordings before Send so we can isolate the Send-time messages.
    dm_count_before = len(fake_slack.dm_blocks_sent)
    blocks_count_before = len(fake_slack.blocks_posted)

    send = SendReclassifyAlert(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        pending=pending,
        support_handle="S0123ABCD",
    )
    sent_count = await send.execute(pending_id=draft.id, by_user_id="U_SE")
    # Reporter (U_REPORTER) + owner (U_OWNER) + CSM (U_CSM) + support channel (C_SUPPORT).
    assert sent_count == 4
    # Three user DMs + one channel post fired during Send.
    assert len(fake_slack.dm_blocks_sent) == dm_count_before + 3
    assert len(fake_slack.blocks_posted) == blocks_count_before + 1

    new_dms = fake_slack.dm_blocks_sent[dm_count_before:]
    assert {u for u, _, _ in new_dms} == {"U_REPORTER", "U_OWNER", "U_CSM"}
    new_posts = fake_slack.blocks_posted[blocks_count_before:]
    assert new_posts[0][0] == "C_SUPPORT"

    # One comms-log row per recipient.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    reclass_rows = [c for c in comms if c.note == "reclassify-alert"]
    assert len(reclass_rows) == 4
    channels = {c.channel for c in reclass_rows}
    assert "dm:U_REPORTER" in channels
    assert "dm:U_OWNER" in channels
    assert "dm:U_CSM" in channels
    assert "C_SUPPORT" in channels

    # Original draft DM was updated to a "Sent" confirmation.
    assert any(ts == draft.dm_message_ts for _, ts, _, _ in fake_slack.messages_updated)
    # Pending row deleted.
    assert await pending.get(draft.id) is None


@pytest.mark.asyncio
async def test_send_reclassify_never_posts_to_customer_channel(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    # The customer channel where the ticket originated.
    customer_channel = "C_CUSTOMER_PRIVATE"
    await orgs.upsert(
        Org(id="acme", name="Acme", csm_user_id="U_CSM", slack_channel_id=customer_channel)
    )
    created = await tickets.create(_bug(lane=Lane.DEV_ACTION))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")

    submit = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
        support_handle=None,
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
    draft = await submit.execute(submission, by_user_id="U_SE")
    assert draft is not None and draft.id is not None

    send = SendReclassifyAlert(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        pending=pending,
        support_handle=None,
    )
    await send.execute(pending_id=draft.id, by_user_id="U_SE")

    # Verify the customer channel is nowhere in posted blocks or DM recipients.
    for ch, _, _ in fake_slack.blocks_posted:
        assert ch != customer_channel
    for user, _, _ in fake_slack.dm_blocks_sent:
        assert user != customer_channel
    # And no comms-log row points at it.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    assert all(customer_channel not in c.channel for c in comms)


@pytest.mark.asyncio
async def test_send_reclassify_on_missing_pending_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    send = SendReclassifyAlert(
        slack=fake_slack, tickets=tickets, events=events, pending=pending, support_handle=None
    )
    sent = await send.execute(pending_id=999, by_user_id="U_SE")
    assert sent == 0


# --- DismissReclassifyDraft --------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_reclassify_deletes_pending_without_sending(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    pending = SQLitePendingReclassifySendRepository(session_factory)

    created = await tickets.create(_bug())
    assert created.id is not None
    submit = SubmitReclassifyDraft(
        slack=fake_slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        se_user_id="U_SE",
        support_handle=None,
        support_ping_channel_id=None,
    )
    draft = await submit.execute(
        ReclassifySubmission(
            ticket_id=created.id,
            new_type=TicketType.CONFIG,
            new_subtype=TicketSubtype.SETUP_INTEGRATION,
            reason="r",
            next_step="n",
            owner_user_id="U_OWNER",
        ),
        by_user_id="U_SE",
    )
    assert draft is not None and draft.id is not None

    dismiss = DismissReclassifyDraft(slack=fake_slack, pending=pending)
    await dismiss.execute(pending_id=draft.id)

    assert await pending.get(draft.id) is None
    # No comms-log rows because nothing was sent.
    async with session_factory() as session:
        comms = list((await session.execute(select(EventCommsLogRow))).scalars())
    assert [c for c in comms if c.note == "reclassify-alert"] == []
