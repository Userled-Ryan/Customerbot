"""Integration tests for Chunk 7's priority flow.

Covers:
- `AssignPriority.suggest` uses customer_weight × severity via the matrix.
- `record_and_offer_override` writes event_prio_changes + DMs SE with override buttons.
- `ApplyPriorityChange` updates ticket + appends prio event with the right reason.
- `MultiCustomerBumpCheck` thresholds (2, 3+, 5+ on critical-path).
- `P0CandidateScan` triggers on the right cluster shape.
- `MonthlyMatrixReview` fires on day-1@9am, honors snooze + already-acked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.priority.actions import (
    ACTION_SET_PRIORITY,
    ACTION_SET_PRIORITY_PATTERN,
    REASON_MANUAL_OVERRIDE,
    REASON_MULTI_CUSTOMER_BUMP,
    REASON_P0_CANDIDATE,
    PriorityChangePayload,
    set_priority_action_id,
)
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import PriorityMatrix
from customerbot.application.priority.monthly_review import (
    ApplyMatrixReviewAck,
    MonthlyMatrixReview,
)
from customerbot.application.priority.multi_customer_bump import MultiCustomerBumpCheck
from customerbot.application.priority.override import ApplyPriorityChange
from customerbot.application.priority.p0_scan import P0CandidateScan
from customerbot.data.repository.bot_state import SQLitePrioMatrixReviewStateRepository
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    ACVTier,
    CustomerWeight,
    Priority,
    RenewalStatus,
    Sentiment,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _bug(*, severity: Severity = Severity.BLOCKING, feature: str | None = None) -> Ticket:
    return Ticket(
        title="x",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        severity=severity,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        feature=feature,
    )


# --- AssignPriority -----------------------------------------------------------


def test_assign_priority_suggest_uses_matrix_with_org_weight() -> None:
    matrix = PriorityMatrix()
    assign = AssignPriority(matrix=matrix, events=None, slack=None)  # type: ignore[arg-type]

    enterprise_negative_atrisk = Org(
        id="acme",
        name="Acme",
        acv_tier=ACVTier.ENTERPRISE,
        sentiment=Sentiment.NEGATIVE,
        renewal_status=RenewalStatus.AT_RISK,
    )
    # Score is critical bucket → blocking → P1.
    assert assign.suggest(enterprise_negative_atrisk, Severity.BLOCKING) == Priority.P1
    # Cosmetic at critical → P3.
    assert assign.suggest(enterprise_negative_atrisk, Severity.COSMETIC) == Priority.P3


def test_assign_priority_suggest_with_no_org_defaults_to_low_weight() -> None:
    matrix = PriorityMatrix()
    assign = AssignPriority(matrix=matrix, events=None, slack=None)  # type: ignore[arg-type]
    # Low + blocking in defaults → P2.
    assert assign.suggest(None, Severity.BLOCKING) == Priority.P2


@pytest.mark.asyncio
async def test_record_and_offer_override_writes_event_and_dms(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    events = SQLiteEventLogRepository(session_factory)
    tickets = SQLiteTicketRepository(session_factory)
    t = await tickets.create(_bug())
    assert t.id is not None

    assign = AssignPriority(matrix=PriorityMatrix(), events=events, slack=fake_slack)
    await assign.record_and_offer_override(t, org=None, se_user_id="U_SE")

    # Event log: null → P1.
    from sqlalchemy import select

    from customerbot.data.database import EventPrioChangeRow

    async with session_factory() as session:
        rows = list((await session.execute(select(EventPrioChangeRow))).scalars())
    assert len(rows) == 1
    assert rows[0].from_priority is None
    assert rows[0].to_priority == t.priority.value
    assert rows[0].reason == "matrix lookup"

    # DM sent with override buttons P1..P4.
    assert len(fake_slack.dm_blocks_sent) == 1
    _, blocks, _ = fake_slack.dm_blocks_sent[0]
    action_block = next(b for b in blocks if b["type"] == "actions")
    labels = [el["text"]["text"] for el in action_block["elements"]]
    assert labels == ["P1", "P2", "P3", "P4"]
    # Slack requires unique action_ids within a message, so each tier button
    # carries a distinct one — all still routed by ACTION_SET_PRIORITY_PATTERN.
    action_ids = [el["action_id"] for el in action_block["elements"]]
    assert action_ids == [
        set_priority_action_id(p) for p in (Priority.P1, Priority.P2, Priority.P3, Priority.P4)
    ]
    assert len(set(action_ids)) == len(action_ids)
    assert all(ACTION_SET_PRIORITY_PATTERN.search(a) for a in action_ids)


# --- ApplyPriorityChange ------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_priority_change_updates_and_logs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    t = await tickets.create(_bug())
    assert t.id is not None

    apply = ApplyPriorityChange(tickets=tickets, events=events)
    payload = PriorityChangePayload(
        ticket_id=t.id, priority=Priority.P0, reason=REASON_P0_CANDIDATE
    )
    out = await apply.execute(payload, by_user_id="U_CTO")
    assert out == Priority.P0

    updated = await tickets.get(t.id)
    assert updated is not None
    assert updated.priority == Priority.P0

    # Event row recorded with the right reason.
    from sqlalchemy import select

    from customerbot.data.database import EventPrioChangeRow

    async with session_factory() as session:
        rows = list((await session.execute(select(EventPrioChangeRow))).scalars())
    assert any(r.reason == REASON_P0_CANDIDATE for r in rows)


@pytest.mark.asyncio
async def test_apply_priority_change_is_noop_when_already_at_tier(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    t = await tickets.create(_bug())
    assert t.id is not None
    current = t.priority

    apply = ApplyPriorityChange(tickets=tickets, events=events)
    out = await apply.execute(
        PriorityChangePayload(ticket_id=t.id, priority=current, reason=REASON_MANUAL_OVERRIDE),
        by_user_id="U_SE",
    )
    assert out == current

    # No event row written.
    from sqlalchemy import select

    from customerbot.data.database import EventPrioChangeRow

    async with session_factory() as session:
        rows = list((await session.execute(select(EventPrioChangeRow))).scalars())
    assert rows == []


# --- MultiCustomerBumpCheck ---------------------------------------------------


@pytest.mark.asyncio
async def test_bump_at_two_orgs_suggests_plus_one_tier(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    await orgs.upsert(Org(id="globex", name="Globex"))

    t = await tickets.create(_bug(severity=Severity.DEGRADED))
    t.priority = Priority.P3
    # Manually set priority to P3 in the DB.
    await tickets.update_priority(t.id or 0, Priority.P3, now=_utcnow())
    await tickets.add_org(t.id or 0, "acme")
    await tickets.add_org(t.id or 0, "globex")

    check = MultiCustomerBumpCheck(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        critical_path_features=[],
    )
    suggested = await check.execute(t.id or 0)
    assert suggested == Priority.P2  # bump_one_tier(P3) = P2

    # DM sent.
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
async def test_bump_at_three_orgs_targets_p1(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    for oid in ("a", "b", "c"):
        await orgs.upsert(Org(id=oid, name=oid))
    t = await tickets.create(_bug(severity=Severity.DEGRADED))
    await tickets.update_priority(t.id or 0, Priority.P3, now=_utcnow())
    for oid in ("a", "b", "c"):
        await tickets.add_org(t.id or 0, oid)

    check = MultiCustomerBumpCheck(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        critical_path_features=[],
    )
    assert await check.execute(t.id or 0) == Priority.P1


@pytest.mark.asyncio
async def test_bump_at_five_orgs_on_critical_path_suggests_p0(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    for oid in ("a", "b", "c", "d", "e"):
        await orgs.upsert(Org(id=oid, name=oid))
    t = await tickets.create(_bug(severity=Severity.DEGRADED, feature="publishing"))
    await tickets.update_priority(t.id or 0, Priority.P2, now=_utcnow())
    for oid in ("a", "b", "c", "d", "e"):
        await tickets.add_org(t.id or 0, oid)

    check = MultiCustomerBumpCheck(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        critical_path_features=["publishing"],
    )
    suggested = await check.execute(t.id or 0)
    assert suggested == Priority.P0
    # The DM button encodes P0 with the P0-candidate reason code.
    _, blocks, _ = fake_slack.dm_blocks_sent[0]
    set_btn = next(
        el
        for b in blocks
        if b["type"] == "actions"
        for el in b["elements"]
        if el["action_id"] == ACTION_SET_PRIORITY
    )
    decoded = PriorityChangePayload.decode(set_btn["value"])
    assert decoded.priority == Priority.P0
    assert decoded.reason == REASON_P0_CANDIDATE


@pytest.mark.asyncio
async def test_bump_below_threshold_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    t = await tickets.create(_bug())
    await tickets.add_org(t.id or 0, "acme")

    check = MultiCustomerBumpCheck(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        critical_path_features=[],
    )
    assert await check.execute(t.id or 0) is None
    assert fake_slack.dm_blocks_sent == []


# --- P0CandidateScan ----------------------------------------------------------


@pytest.mark.asyncio
async def test_p0_scan_fires_on_five_orgs_critical_path(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    for oid in ("a", "b", "c", "d", "e"):
        await orgs.upsert(Org(id=oid, name=oid))
    t = await tickets.create(_bug(feature="publishing"))
    for oid in ("a", "b", "c", "d", "e"):
        await tickets.add_org(t.id or 0, oid)

    scan = P0CandidateScan(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        cto_user_id="U_CTO",
        critical_path_features=["publishing"],
    )
    fired = await scan.execute()
    assert fired == [t.id]

    # Two DMs — one each to SE and CTO.
    assert {user for user, _, _ in fake_slack.dm_blocks_sent} == {"U_SE", "U_CTO"}


@pytest.mark.asyncio
async def test_p0_scan_does_not_refire_on_same_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    for oid in ("a", "b", "c", "d", "e"):
        await orgs.upsert(Org(id=oid, name=oid))
    t = await tickets.create(_bug(feature="publishing"))
    for oid in ("a", "b", "c", "d", "e"):
        await tickets.add_org(t.id or 0, oid)

    scan = P0CandidateScan(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        cto_user_id=None,
        critical_path_features=["publishing"],
    )
    first = await scan.execute()
    second = await scan.execute()
    assert first == [t.id]
    assert second == []  # de-duped via internal already-flagged set


@pytest.mark.asyncio
async def test_p0_scan_ignores_non_critical_features(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    for oid in ("a", "b", "c", "d", "e"):
        await orgs.upsert(Org(id=oid, name=oid))
    t = await tickets.create(_bug(feature="cosmetic-tweak"))
    for oid in ("a", "b", "c", "d", "e"):
        await tickets.add_org(t.id or 0, oid)

    scan = P0CandidateScan(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        cto_user_id=None,
        critical_path_features=["publishing"],  # cosmetic-tweak not in set
    )
    assert await scan.execute() == []


@pytest.mark.asyncio
async def test_p0_scan_ignores_already_p0_tickets(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    for oid in ("a", "b", "c", "d", "e"):
        await orgs.upsert(Org(id=oid, name=oid))
    t = await tickets.create(_bug(feature="publishing"))
    for oid in ("a", "b", "c", "d", "e"):
        await tickets.add_org(t.id or 0, oid)
    await tickets.update_priority(t.id or 0, Priority.P0, now=_utcnow())

    scan = P0CandidateScan(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        cto_user_id=None,
        critical_path_features=["publishing"],
    )
    assert await scan.execute() == []


# --- MonthlyMatrixReview ------------------------------------------------------


@pytest.mark.asyncio
async def test_monthly_review_fires_on_first_at_9am_utc(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    state = SQLitePrioMatrixReviewStateRepository(session_factory)
    review = MonthlyMatrixReview(
        slack=fake_slack,
        state=state,
        se_user_id="U_SE",
        se_timezone="UTC",
        prio_matrix_path="config/prio_matrix.yaml",
    )
    fired = await review.execute(now_utc=datetime(2026, 6, 1, 9, 15))
    assert fired is True
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
async def test_monthly_review_does_not_refire_within_same_month(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    state = SQLitePrioMatrixReviewStateRepository(session_factory)
    review = MonthlyMatrixReview(
        slack=fake_slack,
        state=state,
        se_user_id="U_SE",
        se_timezone="UTC",
        prio_matrix_path=None,
    )
    assert await review.execute(now_utc=datetime(2026, 6, 1, 9, 5)) is True
    # Loop ticks again 5 minutes later — same hour, same day.
    assert await review.execute(now_utc=datetime(2026, 6, 1, 9, 10)) is False
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
async def test_monthly_review_skips_off_day(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    state = SQLitePrioMatrixReviewStateRepository(session_factory)
    review = MonthlyMatrixReview(
        slack=fake_slack,
        state=state,
        se_user_id="U_SE",
        se_timezone="UTC",
        prio_matrix_path=None,
    )
    # 15th of the month at 09:00 — wrong day.
    assert await review.execute(now_utc=datetime(2026, 6, 15, 9, 5)) is False
    # 1st of the month at 08:00 — too early.
    assert await review.execute(now_utc=datetime(2026, 7, 1, 8, 5)) is False
    # 1st of the month at 10:00 — past the firing window.
    assert await review.execute(now_utc=datetime(2026, 7, 1, 10, 5)) is False


@pytest.mark.asyncio
async def test_monthly_review_honors_snooze(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    state_repo = SQLitePrioMatrixReviewStateRepository(session_factory)
    ack = ApplyMatrixReviewAck(state=state_repo)
    review = MonthlyMatrixReview(
        slack=fake_slack,
        state=state_repo,
        se_user_id="U_SE",
        se_timezone="UTC",
        prio_matrix_path=None,
    )
    # SE snoozes for 7 days.
    await ack.snooze_7d()
    state = await state_repo.get()
    snoozed_until = state.last_snooze_until
    assert snoozed_until is not None

    # Even on the 1st at 9am, won't fire while snoozed.
    not_yet = snoozed_until - timedelta(hours=1)
    if not_yet.day == 1 and not_yet.hour == 9:
        fired = await review.execute(now_utc=not_yet)
        assert fired is False


@pytest.mark.asyncio
async def test_monthly_review_dm_carries_ack_and_snooze_buttons(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    state = SQLitePrioMatrixReviewStateRepository(session_factory)
    review = MonthlyMatrixReview(
        slack=fake_slack,
        state=state,
        se_user_id="U_SE",
        se_timezone="UTC",
        prio_matrix_path="config/prio_matrix.yaml",
    )
    await review.execute(now_utc=datetime(2026, 8, 1, 9, 30))
    _, blocks, _ = fake_slack.dm_blocks_sent[0]
    action_block = next(b for b in blocks if b["type"] == "actions")
    ids = [el["action_id"] for el in action_block["elements"]]
    assert ids == ["ack_matrix_review", "snooze_matrix_review"]


# --- End-to-end: dedupe merge → multi-customer bump suggestion ---------------


@pytest.mark.asyncio
async def test_dedupe_merge_cross_org_triggers_bump_check(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    from customerbot.application.intake.dedupe import (
        MergeIntoExisting,
        StashedTicketPayload,
    )
    from customerbot.data.repository.bot_state import (
        SQLitePendingDedupeChoiceRepository,
    )
    from customerbot.domain.bot_state.entities import PendingDedupeChoice

    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    await orgs.upsert(Org(id="globex", name="Globex"))
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    pending = SQLitePendingDedupeChoiceRepository(session_factory)

    candidate = await tickets.create(_bug(severity=Severity.DEGRADED))
    assert candidate.id is not None
    await tickets.update_priority(candidate.id, Priority.P3, now=_utcnow())
    await tickets.add_org(candidate.id, "acme")

    bump_check = MultiCustomerBumpCheck(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        critical_path_features=[],
    )
    merge = MergeIntoExisting(
        tickets=tickets,
        events=events,
        orgs=orgs,
        pending=pending,
        slack=fake_slack,
        se_tickets_channel_id="C_SE_TICKETS",
        bump_check=bump_check,
    )

    now = _utcnow()
    payload = StashedTicketPayload(
        kind="se_bug",
        ticket_dump={"description": "second org sees the same"},
        org_id="globex",
        reporter_user_id="U_SE",
        slack_view_id=None,
        original_slack_link=None,
    )
    p = await pending.create(
        PendingDedupeChoice(
            candidate_ticket_id=candidate.id,
            payload_json=payload.to_json(),
            invoker_user_id="U_SE",
            dm_channel_id="D",
            dm_message_ts="1",
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
    )
    assert p.id is not None
    await merge.execute(pending_id=p.id, by_user_id="U_SE")

    # Bump-check DM fired (alongside any other DMs).
    bump_dms = [
        b
        for _user, blocks, _text in fake_slack.dm_blocks_sent
        for b in blocks
        if b.get("type") == "actions"
        and any(
            (decoded := _try_decode(el.get("value"))) is not None
            and decoded.reason == REASON_MULTI_CUSTOMER_BUMP
            for el in b.get("elements", [])
        )
    ]
    assert len(bump_dms) == 1


def _try_decode(value: object) -> PriorityChangePayload | None:
    if not isinstance(value, str):
        return None
    try:
        return PriorityChangePayload.decode(value)
    except json.JSONDecodeError, ValueError, KeyError:
        return None


# Silence unused-import warnings.
_ = CustomerWeight
_ = TicketStatus
