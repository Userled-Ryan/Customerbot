"""Integration tests for Chunk 11 — customer-comms drafts + nudge jobs.

Covers:
- `comms_drafts` rendering for §9a/§9b/§9c/§9d/§9e (pure functions —
  asserted on body text + block shape).
- `ConfirmationNudgeJob` fires DMs at 24h / 72h / 7d after entering
  awaiting, throttled once per checkpoint, skips non-awaiting tickets,
  and quotes the auto-close date in the body.
- `StatusUpdateCadenceJob` fires on the SLA-tier cadence
  (`status_update_hours`), records the fire in `sla_dm_state`,
  doesn't refire before the next checkpoint, skips tickets without a
  `first_response_at`, and skips priority tiers with no committed
  cadence (P4).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.comms_drafts import (
    Draft,
    auto_close_note,
    initial_ack,
    nudge_for_confirmation,
    resolution,
    status_update,
)
from customerbot.application.tracking.nudges import (
    ConfirmationNudgeJob,
    StatusUpdateCadenceJob,
)
from customerbot.config import _default_sla_targets
from customerbot.data.repository.bot_state import SQLiteSLADMStateRepository
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.bot_state.entities import SLAStage
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _ts(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute)


def _bug(
    *,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    priority: Priority = Priority.P2,
    severity: Severity = Severity.BLOCKING,
    title: str = "Checkout broken",
    description: str = "users hang on submit",
    created_at: datetime | None = None,
    first_response_at: datetime | None = None,
    ticket_type: TicketType = TicketType.BUG,
    subtype: TicketSubtype = TicketSubtype.PLATFORM_WIDE,
) -> Ticket:
    return Ticket(
        title=title,
        type=ticket_type,
        subtype=subtype,
        status=status,
        priority=priority,
        severity=severity,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description=description,
        created_at=created_at or _ts(2026, 6, 1, 9, 0),
        first_response_at=first_response_at,
    )


# --- Pure template rendering --------------------------------------------------


def test_initial_ack_includes_org_name_and_type() -> None:
    ticket = _bug()
    org = Org(id="acme", name="Acme Corp")
    draft = initial_ack(ticket, org)
    assert isinstance(draft, Draft)
    assert "Acme Corp" in draft.headline
    assert "Bug" in draft.body
    assert "[first name]" in draft.body
    # Blocks always lead with a writing-hand banner so SE can distinguish
    # drafts from regular bot DMs.
    blocks = draft.blocks()
    assert blocks[0]["text"]["text"].startswith(":writing_hand:")


def test_initial_ack_truncates_long_description() -> None:
    ticket = _bug(description="x" * 600)
    draft = initial_ack(ticket, None)
    assert "…" in draft.body
    assert "the customer" in draft.headline


def test_status_update_with_internal_note_uses_note() -> None:
    ticket = _bug()
    draft = status_update(ticket, latest_internal_note="Repro confirmed; isolating to billing svc.")
    assert "Repro confirmed" in draft.body
    assert ticket.display_id in draft.body


def test_status_update_without_note_includes_checkpoint() -> None:
    ticket = _bug()
    cp = _ts(2026, 6, 2, 17, 0)
    draft = status_update(ticket, next_checkpoint=cp)
    assert "Still investigating" in draft.body
    # Date roundtrip via strftime — month abbreviation present.
    assert "Jun" in draft.body


def test_resolution_bug_variant() -> None:
    ticket = _bug()
    draft = resolution(ticket, via_hotfix=False)
    assert "shipped a fix" in draft.body
    assert "auto-close in 7 days" in draft.body


def test_resolution_config_variant() -> None:
    ticket = _bug(ticket_type=TicketType.CONFIG, subtype=TicketSubtype.SETUP_INTEGRATION)
    draft = resolution(ticket, via_hotfix=False)
    assert "Setup is complete" in draft.body


def test_resolution_hotfix_variant_mentions_underlying_bug() -> None:
    ticket = _bug()
    draft = resolution(ticket, via_hotfix=True)
    assert "hotfix" in draft.body.lower()
    assert "underlying bug" in draft.body.lower()


def test_nudge_for_confirmation_quotes_auto_close_date() -> None:
    ticket = _bug()
    target_date = date(2026, 6, 15)
    draft = nudge_for_confirmation(ticket, auto_close_at=target_date)
    assert ticket.display_id in draft.body
    assert "auto-close" in draft.body
    # The date should render in the body.
    assert "Jun 2026" in draft.body


def test_auto_close_note_short_and_mentions_30d_reopen() -> None:
    ticket = _bug()
    draft = auto_close_note(ticket)
    assert "30 days" in draft.body
    assert ticket.display_id in draft.body


# --- ConfirmationNudgeJob ----------------------------------------------------


@pytest.mark.asyncio
async def test_confirmation_nudge_fires_at_24h_then_72h(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert created.id is not None
    entered_at = _ts(2026, 6, 1, 9, 0)
    await events.append_status_change(
        created.id,
        TicketStatus.IN_PROGRESS,
        TicketStatus.AWAITING_CUSTOMER,
        "U_SE",
        entered_at,
    )

    job = ConfirmationNudgeJob(
        tickets=tickets, events=events, sla_state=sla, slack=fake_slack, se_user_id="U_SE"
    )

    # 12h in — too early, no nudges.
    out = await job.execute(now=entered_at + timedelta(hours=12))
    assert out.fired == []
    assert fake_slack.dm_blocks_sent == []

    # 25h in — 24h checkpoint fires.
    out = await job.execute(now=entered_at + timedelta(hours=25))
    assert out.fired == [(created.id, SLAStage.SE_NUDGE_24H)]
    assert len(fake_slack.dm_blocks_sent) == 1

    # 26h in — same checkpoint, throttled.
    fake_slack.dm_blocks_sent.clear()
    out = await job.execute(now=entered_at + timedelta(hours=26))
    assert out.fired == []
    assert fake_slack.dm_blocks_sent == []

    # 73h in — 72h checkpoint fires (24h still throttled).
    out = await job.execute(now=entered_at + timedelta(hours=73))
    assert out.fired == [(created.id, SLAStage.SE_NUDGE_72H)]
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
async def test_confirmation_nudge_fires_7d_marker(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert created.id is not None
    entered_at = _ts(2026, 6, 1, 9, 0)
    await events.append_status_change(
        created.id,
        TicketStatus.IN_PROGRESS,
        TicketStatus.AWAITING_CUSTOMER,
        "U_SE",
        entered_at,
    )

    job = ConfirmationNudgeJob(
        tickets=tickets, events=events, sla_state=sla, slack=fake_slack, se_user_id="U_SE"
    )
    out = await job.execute(now=entered_at + timedelta(days=7, hours=1))
    fired_stages = {stage for _id, stage in out.fired}
    # All three checkpoints fire in one pass when we cross them all.
    assert fired_stages == {SLAStage.SE_NUDGE_24H, SLAStage.SE_NUDGE_72H, SLAStage.SE_NUDGE_7D}
    assert len(fake_slack.dm_blocks_sent) == 3


@pytest.mark.asyncio
async def test_confirmation_nudge_skips_non_awaiting_tickets(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    # An in-progress ticket should be invisible to the confirmation-nudge job.
    await tickets.create(_bug(status=TicketStatus.IN_PROGRESS))

    job = ConfirmationNudgeJob(
        tickets=tickets, events=events, sla_state=sla, slack=fake_slack, se_user_id="U_SE"
    )
    out = await job.execute(now=_ts(2026, 7, 1, 9, 0))
    assert out.fired == []
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_confirmation_nudge_quotes_auto_close_date_in_dm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    created = await tickets.create(_bug(status=TicketStatus.AWAITING_CUSTOMER))
    assert created.id is not None
    entered_at = _ts(2026, 6, 1, 9, 0)
    await events.append_status_change(
        created.id,
        TicketStatus.IN_PROGRESS,
        TicketStatus.AWAITING_CUSTOMER,
        "U_SE",
        entered_at,
    )

    job = ConfirmationNudgeJob(
        tickets=tickets, events=events, sla_state=sla, slack=fake_slack, se_user_id="U_SE"
    )
    await job.execute(now=entered_at + timedelta(hours=25))
    assert len(fake_slack.dm_blocks_sent) == 1
    _user, blocks, _text = fake_slack.dm_blocks_sent[0]
    # Auto-close date = entered_at + 7 days = 2026-06-08.
    body_block = next(b for b in blocks if b.get("type") == "section" and "Jun 2026" in str(b))
    assert "Jun 2026" in str(body_block)


# --- StatusUpdateCadenceJob ---------------------------------------------------


@pytest.mark.asyncio
async def test_status_update_cadence_fires_after_p1_target(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    # P1 status_update_hours = 24h (default config).
    first_response = _ts(2026, 6, 1, 9, 0)
    created = await tickets.create(
        _bug(
            priority=Priority.P1,
            created_at=first_response,
            first_response_at=first_response,
        )
    )
    assert created.id is not None

    job = StatusUpdateCadenceJob(
        tickets=tickets,
        sla_state=sla,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    # 12h in — too early.
    out = await job.execute(now=first_response + timedelta(hours=12))
    assert out.fired == []
    # 25h in — fires.
    out = await job.execute(now=first_response + timedelta(hours=25))
    assert out.fired == [created.id]
    assert len(fake_slack.dm_blocks_sent) == 1
    # Within the next 24h window — throttled.
    fake_slack.dm_blocks_sent.clear()
    out = await job.execute(now=first_response + timedelta(hours=30))
    assert out.fired == []
    # 25h + 24h later — fires again.
    out = await job.execute(now=first_response + timedelta(hours=50))
    assert out.fired == [created.id]


@pytest.mark.asyncio
async def test_status_update_cadence_skips_without_first_response(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    await tickets.create(_bug(priority=Priority.P1, first_response_at=None))

    job = StatusUpdateCadenceJob(
        tickets=tickets,
        sla_state=sla,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    out = await job.execute(now=_ts(2026, 7, 1, 9, 0))
    assert out.fired == []
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_status_update_cadence_skips_p4_no_target(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    # P4 has status_update_hours = None.
    first_response = _ts(2026, 6, 1, 9, 0)
    await tickets.create(
        _bug(
            priority=Priority.P4,
            created_at=first_response,
            first_response_at=first_response,
        )
    )

    job = StatusUpdateCadenceJob(
        tickets=tickets,
        sla_state=sla,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    # Even way past the would-be cadence — no fire.
    out = await job.execute(now=first_response + timedelta(days=30))
    assert out.fired == []
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_status_update_cadence_skips_non_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    sla = SQLiteSLADMStateRepository(session_factory)
    first_response = _ts(2026, 6, 1, 9, 0)
    # Awaiting customer — cadence doesn't apply.
    await tickets.create(
        _bug(
            status=TicketStatus.AWAITING_CUSTOMER,
            priority=Priority.P1,
            created_at=first_response,
            first_response_at=first_response,
        )
    )

    job = StatusUpdateCadenceJob(
        tickets=tickets,
        sla_state=sla,
        slack=fake_slack,
        se_user_id="U_SE",
        sla_targets=_default_sla_targets(),
    )
    out = await job.execute(now=first_response + timedelta(hours=48))
    assert out.fired == []
    assert fake_slack.dm_blocks_sent == []
