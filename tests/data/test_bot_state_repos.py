from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.repository.bot_state import (
    SQLiteChannelOrgCacheRepository,
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
    SQLitePendingPrioOverrideRepository,
    SQLitePrioMatrixReviewStateRepository,
    SQLiteSLADMStateRepository,
)
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.bot_state.entities import (
    ChannelOrgEntry,
    DraftFormSession,
    ModalKind,
    PendingDedupeChoice,
    PendingPrioOverride,
    PrioMatrixReviewState,
    SLAStage,
    SLAState,
)
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ticket() -> Ticket:
    return Ticket(
        title="x",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        severity=Severity.BLOCKING,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
    )


# --- DraftFormSession ---


@pytest.mark.asyncio
async def test_draft_form_session_create_and_lookup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteDraftFormSessionRepository(session_factory)
    now = _utcnow()
    created = await repo.create(
        DraftFormSession(
            slack_view_id="V123",
            modal_kind=ModalKind.SE_BUG,
            invoker_user_id="U_SE",
            invoker_channel_id="C_ACME",
            invoker_thread_ts="1700.123",
            payload_json='{"summary":"foo"}',
            created_at=now,
            expires_at=now + timedelta(minutes=30),
        )
    )
    assert created.id is not None

    got = await repo.get_by_view_id("V123")
    assert got is not None
    assert got.modal_kind == ModalKind.SE_BUG
    assert got.payload_json == '{"summary":"foo"}'


@pytest.mark.asyncio
async def test_draft_form_session_delete_expired(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteDraftFormSessionRepository(session_factory)
    now = _utcnow()
    fresh = await repo.create(
        DraftFormSession(
            slack_view_id="V_fresh",
            modal_kind=ModalKind.SE_BUG,
            invoker_user_id="U_SE",
            created_at=now,
            expires_at=now + timedelta(minutes=30),
        )
    )
    stale = await repo.create(
        DraftFormSession(
            slack_view_id="V_stale",
            modal_kind=ModalKind.SE_BUG,
            invoker_user_id="U_SE",
            created_at=now - timedelta(hours=1),
            expires_at=now - timedelta(minutes=1),
        )
    )
    assert fresh.id and stale.id

    deleted = await repo.delete_expired(now=now)
    assert deleted == 1

    assert await repo.get_by_view_id("V_fresh") is not None
    assert await repo.get_by_view_id("V_stale") is None


# --- ChannelOrgCache ---


@pytest.mark.asyncio
async def test_channel_org_cache_upsert_and_get(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # FK to orgs.id, so seed an org first.
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    cache = SQLiteChannelOrgCacheRepository(session_factory)
    now = _utcnow()
    await cache.upsert(
        ChannelOrgEntry(slack_channel_id="C_ACME", org_id="acme", last_synced_at=now)
    )

    got = await cache.get("C_ACME")
    assert got is not None
    assert got.org_id == "acme"

    # Negative cache entry (channel has no org).
    await cache.upsert(
        ChannelOrgEntry(slack_channel_id="C_RANDOM", org_id=None, last_synced_at=now)
    )
    miss = await cache.get("C_RANDOM")
    assert miss is not None
    assert miss.org_id is None


# --- SLA DM state ---


@pytest.mark.asyncio
async def test_sla_dm_state_upsert_and_get(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    t = await tickets.create(_ticket())
    assert t.id is not None

    repo = SQLiteSLADMStateRepository(session_factory)
    now = _utcnow()
    assert await repo.get(t.id, SLAStage.FIRST_RESPONSE) is None

    await repo.upsert(t.id, SLAStage.FIRST_RESPONSE, SLAState.AMBER, last_dm_at=now, now=now)
    got = await repo.get(t.id, SLAStage.FIRST_RESPONSE)
    assert got is not None
    assert got.last_state == SLAState.AMBER

    # Idempotent: update to RED.
    later = now + timedelta(hours=1)
    await repo.upsert(t.id, SLAStage.FIRST_RESPONSE, SLAState.RED, last_dm_at=later, now=later)
    got = await repo.get(t.id, SLAStage.FIRST_RESPONSE)
    assert got is not None
    assert got.last_state == SLAState.RED


# --- PendingDedupeChoice ---


@pytest.mark.asyncio
async def test_pending_dedupe_choice_round_trip_and_expiry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    candidate = await tickets.create(_ticket())
    assert candidate.id is not None

    repo = SQLitePendingDedupeChoiceRepository(session_factory)
    now = _utcnow()
    created = await repo.create(
        PendingDedupeChoice(
            candidate_ticket_id=candidate.id,
            payload_json='{"summary":"dupe?"}',
            invoker_user_id="U_SE",
            dm_channel_id="D123",
            dm_message_ts="1700.1",
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
    )
    assert created.id is not None
    got = await repo.get(created.id)
    assert got is not None
    assert got.candidate_ticket_id == candidate.id

    # Expire it manually by creating a stale row, then sweep.
    await repo.create(
        PendingDedupeChoice(
            candidate_ticket_id=candidate.id,
            payload_json="{}",
            invoker_user_id="U_SE",
            dm_channel_id="D123",
            dm_message_ts="1700.9",
            created_at=now - timedelta(days=10),
            expires_at=now - timedelta(days=1),
        )
    )
    assert await repo.delete_expired(now=now) == 1

    # Explicit delete.
    await repo.delete(created.id)
    assert await repo.get(created.id) is None


# --- PendingPrioOverride ---


@pytest.mark.asyncio
async def test_pending_prio_override_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    t = await tickets.create(_ticket())
    assert t.id is not None

    repo = SQLitePendingPrioOverrideRepository(session_factory)
    now = _utcnow()
    created = await repo.create(
        PendingPrioOverride(
            ticket_id=t.id,
            suggested_priority="P2",
            dm_channel_id="D",
            dm_message_ts="1",
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
    )
    assert created.id is not None

    got = await repo.get(created.id)
    assert got is not None
    assert got.suggested_priority == "P2"

    await repo.delete(created.id)
    assert await repo.get(created.id) is None


# --- PrioMatrixReviewState (singleton) ---


@pytest.mark.asyncio
async def test_prio_matrix_review_state_singleton_lazy_init(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLitePrioMatrixReviewStateRepository(session_factory)
    # First read creates the singleton row.
    state = await repo.get()
    assert state.last_ack_at is None
    assert state.last_snooze_until is None

    # Second read returns the same row, no duplicates.
    state2 = await repo.get()
    assert state2.last_ack_at is None


@pytest.mark.asyncio
async def test_prio_matrix_review_state_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLitePrioMatrixReviewStateRepository(session_factory)
    now = _utcnow()
    snooze_until = now + timedelta(days=7)
    await repo.update(
        PrioMatrixReviewState(last_ack_at=None, last_snooze_until=snooze_until),
        now=now,
    )
    state = await repo.get()
    assert state.last_snooze_until is not None
    assert (state.last_snooze_until - snooze_until).total_seconds() < 0.001
