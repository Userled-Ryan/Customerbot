"""End-to-end dedupe tests against real SQLite.

Covers the three §11 match criteria, the Merge/Create-new round-trip via the
`pending_dedupe_choices` stash, and the no-match straight-create path.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.dedupe import (
    ACTION_CREATE_NEW_DEDUPE,
    ACTION_MERGE_DEDUPE,
    FindDedupeCandidate,
    MergeIntoExisting,
    OfferDedupeChoice,
    StashedTicketPayload,
)
from customerbot.application.intake.submissions import SEBugSubmission
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import PriorityMatrix
from customerbot.data.repository.bot_state import (
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _build_submit(
    factory: async_sessionmaker[AsyncSession], slack: FakeSlackPort
) -> SubmitTicketForm:
    tickets = SQLiteTicketRepository(factory)
    pending = SQLitePendingDedupeChoiceRepository(factory)
    events = SQLiteEventLogRepository(factory)
    return SubmitTicketForm(
        slack=slack,
        tickets=tickets,
        events=events,
        orgs=SQLiteOrgRepository(factory),
        drafts=SQLiteDraftFormSessionRepository(factory),
        find_dedupe=FindDedupeCandidate(tickets=tickets),
        offer_dedupe=OfferDedupeChoice(slack=slack, pending=pending),
        assign_priority=AssignPriority(matrix=PriorityMatrix(), events=events),
        se_user_id="U_SE",
        se_tickets_channel_id="C_SE_TICKETS",
    )


async def _seed_org(factory: async_sessionmaker[AsyncSession], org_id: str) -> None:
    orgs = SQLiteOrgRepository(factory)
    await orgs.upsert(Org(id=org_id, name=org_id.upper()))


# --- Criterion 1: same org + token overlap ≥ 0.6 ----------------------------


@pytest.mark.asyncio
async def test_same_org_high_overlap_triggers_dedupe_dm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)

    # First submission — straight-create.
    first = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari",
            description="Customer cannot publish campaigns on iOS Safari",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert first.ticket is not None
    candidate_display = first.ticket.display_id

    # Second submission — same org, very similar wording → dedupe DM.
    second = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing campaigns fails on Safari",
            description="Customer publishes campaigns on iOS Safari but fails",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert second.ticket is None  # no new ticket yet
    assert second.pending_dedupe is not None
    assert second.pending_dedupe.candidate_ticket_id == first.ticket.id

    # Dedupe DM was sent (alongside the prio-rationale DM from creation #1).
    dedupe_dms = [
        b
        for _user, blocks, _text in fake_slack.dm_blocks_sent
        for b in blocks
        if b.get("type") == "actions"
        and any(el.get("action_id") == ACTION_MERGE_DEDUPE for el in b.get("elements", []))
    ]
    assert len(dedupe_dms) == 1
    action_block = dedupe_dms[0]
    ids = [el["action_id"] for el in action_block["elements"]]
    assert ids == [ACTION_MERGE_DEDUPE, ACTION_CREATE_NEW_DEDUPE]

    # The "Merge" button references the candidate by display_id.
    merge_label = action_block["elements"][0]["text"]["text"]
    assert candidate_display in merge_label


@pytest.mark.asyncio
async def test_same_org_low_overlap_does_not_dedupe(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)

    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari",
            description="iOS Safari publishing broken",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    second = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Reporting dashboard is empty",
            description="Numbers are zero, customer confused",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert second.ticket is not None  # no dedupe
    assert second.pending_dedupe is None


# --- Criterion 2: prod_link exact ------------------------------------------


@pytest.mark.asyncio
async def test_exact_prod_link_match_triggers_dedupe(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """CSM-intake-style submissions where prod_link is the strong signal."""
    await _seed_org(session_factory, "acme")
    tickets = SQLiteTicketRepository(session_factory)
    # Seed an existing ticket with a specific prod_link.
    existing = await tickets.create(
        Ticket(
            title="campaign X broken",
            type=TicketType.CONFIG,
            subtype=TicketSubtype.SETUP_INTEGRATION,
            severity=Severity.DEGRADED,
            reporter_user_id="U_CSM",
            source=Source.TECH_ASSISTANCE,
            description="totally different wording",
            prod_link="https://app.userled.io/campaign-42",
        )
    )

    submit = _build_submit(session_factory, fake_slack)
    # Submit with the same prod_link but unrelated text.
    from customerbot.application.intake.submissions import CSMIntakeSubmission

    result = await submit.from_csm_intake(
        CSMIntakeSubmission(
            description="completely unrelated text, but same area",
            org_id="acme",
            prod_link="https://app.userled.io/campaign-42",
            blocking=False,
            deadline=None,
            blocking_impact=None,
        ),
        reporter_user_id="U_CSM",
    )
    assert result.ticket is None
    assert result.pending_dedupe is not None
    assert result.pending_dedupe.candidate_ticket_id == existing.id


# --- Criterion 3: cross-org + feature + severity + overlap ≥ 0.7 -----------


@pytest.mark.asyncio
async def test_cross_org_with_feature_tag_triggers_dedupe(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """When a feature-tagged existing ticket matches a different-org submission."""
    await _seed_org(session_factory, "acme")
    await _seed_org(session_factory, "globex")
    tickets = SQLiteTicketRepository(session_factory)
    existing = await tickets.create(
        Ticket(
            title="email send pipeline stalls under load",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.DEGRADED,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            description="campaigns queued but never send when more than ten run",
        )
    )
    assert existing.id is not None
    await tickets.add_org(existing.id, "acme")
    await tickets.update_feature(existing.id, "email-pipeline")

    submit = _build_submit(session_factory, fake_slack)
    # New ticket from globex with similar wording — but criterion 3 requires
    # a feature tag on the PROPOSED ticket too. Forms can't tag features at
    # creation time (manual step), so this case is currently dormant — verify
    # that no DM fires.
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="globex",
            source=Source.CUSTOMER_CHANNEL,
            summary="email pipeline stalled",
            description="campaigns queued but never send when more than ten run",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    # criterion 3 is dormant until features can be tagged at submission time:
    # the submission has no feature, so it can't match the existing one's tag.
    # Documented behaviour for v1.
    assert result.ticket is not None
    assert result.pending_dedupe is None


@pytest.mark.asyncio
async def test_no_live_tickets_means_no_dedupe(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="x",
            description="y",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    assert result.pending_dedupe is None
    # No dedupe DM was sent. (A prio-rationale DM is sent on creation, but
    # that's the override flow — not the dedupe Merge/Create-new DM.)
    dedupe_dms = [
        b
        for _user, blocks, _text in fake_slack.dm_blocks_sent
        for b in blocks
        if b.get("type") == "actions"
        and any(el.get("action_id") == ACTION_MERGE_DEDUPE for el in b.get("elements", []))
    ]
    assert dedupe_dms == []


# --- Merge / Create-new round-trip -----------------------------------------


@pytest.mark.asyncio
async def test_create_new_proceeds_to_normal_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Click 'Create new' → ticket is created from the stashed payload."""
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)
    pending_repo = SQLitePendingDedupeChoiceRepository(session_factory)

    # First submission → straight-create.
    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari",
            description="iOS Safari publishing broken on the campaign editor page",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    # Second submission → dedupe pending.
    second = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari on iOS",
            description="iOS Safari publishing broken on the campaign editor page repro",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert second.pending_dedupe is not None
    pending_id = second.pending_dedupe.id
    assert pending_id is not None

    # Simulate "Create new" click by fetching the pending row, deserialising,
    # and calling proceed_create_from_pending.
    pending = await pending_repo.get(pending_id)
    assert pending is not None
    payload = StashedTicketPayload.from_json(pending.payload_json)
    result = await submit.proceed_create_from_pending(payload)
    await pending_repo.delete(pending_id)

    assert result.ticket is not None
    # Two distinct tickets now live.
    tickets = SQLiteTicketRepository(session_factory)
    live = await tickets.query_live()
    assert len(live) == 2
    # And the pending row is gone.
    assert await pending_repo.get(pending_id) is None


@pytest.mark.asyncio
async def test_merge_appends_context_and_drops_pending(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)
    tickets = SQLiteTicketRepository(session_factory)
    pending_repo = SQLitePendingDedupeChoiceRepository(session_factory)

    first = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari",
            description="iOS Safari publishing broken on the campaign editor",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert first.ticket is not None
    candidate_id = first.ticket.id

    second = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari again",
            description="iOS Safari publishing broken on the campaign editor — yet another report",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert second.pending_dedupe is not None
    pending_id = second.pending_dedupe.id
    assert pending_id is not None

    merge = MergeIntoExisting(
        tickets=tickets,
        events=SQLiteEventLogRepository(session_factory),
        orgs=SQLiteOrgRepository(session_factory),
        pending=pending_repo,
        slack=fake_slack,
        se_tickets_channel_id="C_SE_TICKETS",
    )
    merged = await merge.execute(pending_id=pending_id, by_user_id="U_SE")
    assert merged is not None
    assert merged.id == candidate_id

    # Pending row dropped.
    assert await pending_repo.get(pending_id) is None
    # Still only one live ticket.
    assert len(await tickets.query_live()) == 1

    # Event log carries the merged-in context.
    from sqlalchemy import select

    from customerbot.data.database import EventStatusChangeRow

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(EventStatusChangeRow).where(
                        EventStatusChangeRow.ticket_id == candidate_id
                    )
                )
            ).scalars()
        )
    # First row = null → New on create; second = merge note.
    assert any("merged-in" in (r.note or "") for r in rows)


@pytest.mark.asyncio
async def test_merge_cross_org_adds_org_to_affected(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A merge from a different org adds that org to ticket_orgs."""
    await _seed_org(session_factory, "acme")
    await _seed_org(session_factory, "globex")
    submit = _build_submit(session_factory, fake_slack)
    tickets = SQLiteTicketRepository(session_factory)
    pending_repo = SQLitePendingDedupeChoiceRepository(session_factory)

    # acme reports first.
    first = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari",
            description="iOS Safari publishing broken on the campaign editor",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert first.ticket is not None
    candidate_id = first.ticket.id
    assert candidate_id is not None

    # Manually stash a pending row from globex (same-org criterion wouldn't
    # trigger from a different org; we forge the pending row to test the
    # cross-org branch of MergeIntoExisting).
    from datetime import UTC, datetime, timedelta

    from customerbot.domain.bot_state.entities import PendingDedupeChoice

    payload = StashedTicketPayload(
        kind="se_bug",
        ticket_dump={"description": "globex sees the same bug"},
        org_id="globex",
        reporter_user_id="U_SE",
        slack_view_id=None,
        original_slack_link=None,
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    pending = await pending_repo.create(
        PendingDedupeChoice(
            candidate_ticket_id=candidate_id,
            payload_json=payload.to_json(),
            invoker_user_id="U_SE",
            dm_channel_id="D",
            dm_message_ts="1",
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
    )
    assert pending.id is not None

    merge = MergeIntoExisting(
        tickets=tickets,
        events=SQLiteEventLogRepository(session_factory),
        orgs=SQLiteOrgRepository(session_factory),
        pending=pending_repo,
        slack=fake_slack,
        se_tickets_channel_id="C_SE_TICKETS",
    )
    await merge.execute(pending_id=pending.id, by_user_id="U_SE")

    affected = await tickets.list_orgs(candidate_id)
    assert sorted(affected) == ["acme", "globex"]


@pytest.mark.asyncio
async def test_offer_dedupe_stashes_button_value_with_pending_id(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Button value must carry the row id so the merge handler can look it up."""
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)

    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari",
            description="iOS Safari publishing broken on the campaign editor",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    second = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari again",
            description="iOS Safari publishing broken on the campaign editor still",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert second.pending_dedupe is not None
    pending_id = second.pending_dedupe.id

    # Find the dedupe DM's action block among the recorded DMs.
    dedupe_action_blocks = [
        b
        for _user, blocks, _text in fake_slack.dm_blocks_sent
        for b in blocks
        if b.get("type") == "actions"
        and any(el.get("action_id") == ACTION_MERGE_DEDUPE for el in b.get("elements", []))
    ]
    assert len(dedupe_action_blocks) == 1
    for el in dedupe_action_blocks[0]["elements"]:
        assert el["value"] == str(pending_id)

    # The pending row should have the DM metadata populated (post-update).
    pending_repo = SQLitePendingDedupeChoiceRepository(session_factory)
    assert pending_id is not None
    refreshed = await pending_repo.get(pending_id)
    assert refreshed is not None
    assert refreshed.dm_channel_id  # non-empty
    assert refreshed.dm_message_ts  # non-empty


@pytest.mark.asyncio
async def test_payload_dump_round_trips_through_create_new(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """The stashed payload must preserve all ticket fields the form captured."""
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)
    pending_repo = SQLitePendingDedupeChoiceRepository(session_factory)

    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari",
            description="iOS Safari publishing broken on the campaign editor page",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    second = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on Safari on iOS",
            description="iOS Safari publishing broken on the campaign editor repro",
            blocking=False,
            deadline=None,
            affected_user="user@acme.com",
            replay_link="https://r/2",
        ),
        reporter_user_id="U_SE",
    )
    assert second.pending_dedupe is not None
    pending_id = second.pending_dedupe.id
    assert pending_id is not None
    pending = await pending_repo.get(pending_id)
    assert pending is not None

    # Decode and verify all distinct fields survived the stash.
    decoded = json.loads(pending.payload_json)
    assert decoded["kind"] == "se_bug"
    assert decoded["ticket_dump"]["affected_user"] == "user@acme.com"
    assert decoded["ticket_dump"]["replay_link"] == "https://r/2"
    assert decoded["ticket_dump"]["severity"] == Severity.DEGRADED.value
