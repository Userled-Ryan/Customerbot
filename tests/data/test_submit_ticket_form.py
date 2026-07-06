from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.dedupe import (
    FindDedupeCandidate,
    OfferDedupeChoice,
)
from customerbot.application.intake.submissions import (
    CSMIntakeSubmission,
    SEBugSubmission,
)
from customerbot.application.intake.submit_ticket_form import (
    OrgCreationError,
    SubmitTicketForm,
)
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import PriorityMatrix
from customerbot.data.repository.bot_state import (
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import (
    ACVTier,
    Lane,
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


def _build(
    factory: async_sessionmaker[AsyncSession],
    slack: FakeSlackPort,
    *,
    se_tickets_channel_id: str | None = "C_SE_TICKETS",
) -> SubmitTicketForm:
    tickets = SQLiteTicketRepository(factory)
    events = SQLiteEventLogRepository(factory)
    return SubmitTicketForm(
        slack=slack,
        tickets=tickets,
        events=events,
        orgs=SQLiteOrgRepository(factory),
        drafts=SQLiteDraftFormSessionRepository(factory),
        find_dedupe=FindDedupeCandidate(tickets=tickets),
        offer_dedupe=OfferDedupeChoice(
            slack=slack, pending=SQLitePendingDedupeChoiceRepository(factory)
        ),
        assign_priority=AssignPriority(matrix=PriorityMatrix(), events=events),
        se_user_id="U_SE",
        se_tickets_channel_id=se_tickets_channel_id,
    )


@pytest.mark.asyncio
async def test_se_bug_happy_path(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on iOS",
            description="Detailed description.",
            blocking=True,
            deadline=None,
            affected_user="user@acme.com",
            replay_link="https://replay/123",
        ),
        reporter_user_id="U_SE",
        slack_view_id="V_TEST",
        original_slack_link="https://slack/p123",
    )
    assert result.ticket is not None
    ticket = result.ticket

    # Step 4 — ticket persisted.
    assert ticket.id is not None
    assert ticket.title == "Publishing fails on iOS"
    assert ticket.type == TicketType.BUG
    assert ticket.severity == Severity.BLOCKING
    assert ticket.status == TicketStatus.NEW

    # Org M2M linked.
    tickets = SQLiteTicketRepository(session_factory)
    assert await tickets.list_orgs(ticket.id) == ["acme"]

    # Step 6 — SE adjusts priority from the ticket card's dropdown, so no
    # override DM is sent to the SE.
    assert not any(user == "U_SE" for user, _blocks, _text in fake_slack.dm_blocks_sent)

    # Step 7 — ticket card posted to SE_TICKETS_CHANNEL_ID.
    assert len(fake_slack.blocks_posted) == 1
    channel, blocks, _text = fake_slack.blocks_posted[0]
    assert channel == "C_SE_TICKETS"
    assert any(b.get("type") == "actions" for b in blocks)

    # Card ts persisted on the ticket.
    assert result.card_message_ts == fake_slack.next_message_ts


@pytest.mark.asyncio
async def test_se_config_ticket_defaults_to_p4(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A non-urgent Config ticket bypasses the customer-weight matrix and lands
    at P4 regardless of the org's weight."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(
        Org(
            id="acme",
            name="Acme Corp",
            acv_tier=ACVTier.ENTERPRISE,
            sentiment=Sentiment.NEGATIVE,
            renewal_status=RenewalStatus.AT_RISK,
        )
    )

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CALL,
            summary="Enable LinkedIn ads behind feature flag",
            description="Turn on LI ads for this org.",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
            ticket_type=TicketType.CONFIG,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    ticket = result.ticket
    assert ticket.type == TicketType.CONFIG
    assert ticket.subtype == TicketSubtype.SETUP_INTEGRATION
    assert ticket.priority == Priority.P4


@pytest.mark.asyncio
async def test_se_config_ticket_urgent_bumps_to_p2(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """An urgent Config ticket is capped at P2 — never higher."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CALL,
            summary="Verify domain before Monday launch",
            description="",
            blocking=True,
            deadline=date(2026, 7, 6),
            affected_user=None,
            replay_link=None,
            ticket_type=TicketType.CONFIG,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    assert result.ticket.type == TicketType.CONFIG
    assert result.ticket.priority == Priority.P2
    assert result.ticket.deadline == date(2026, 7, 6)


@pytest.mark.asyncio
async def test_se_product_change_ticket_defaults_to_p4_se_lane(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A Product-change ticket (prod improvement / enhancement) is priced and
    laned like Config: bypasses the customer-weight matrix, defaults to P4, SE
    lane, ENHANCEMENT subtype (SE reclassifies to new-capability if needed)."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(
        Org(
            id="acme",
            name="Acme Corp",
            acv_tier=ACVTier.ENTERPRISE,
            sentiment=Sentiment.NEGATIVE,
            renewal_status=RenewalStatus.AT_RISK,
        )
    )

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.PRODUCT_CHANNEL,
            summary="Add bulk export to the campaigns dashboard",
            description="Customer asked for a CSV export button.",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
            ticket_type=TicketType.FEATURE_REQUEST,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    ticket = result.ticket
    assert ticket.type == TicketType.FEATURE_REQUEST
    assert ticket.subtype == TicketSubtype.ENHANCEMENT
    assert ticket.priority == Priority.P4
    assert ticket.lane == Lane.SE_ACTION
    assert ticket.source == Source.PRODUCT_CHANNEL


@pytest.mark.asyncio
async def test_se_product_change_ticket_urgent_bumps_to_p2(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """An urgent Product-change ticket is capped at P2, same rule as Config."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.PRODUCT_CHANNEL,
            summary="Ship the new onboarding flow before launch",
            description="",
            blocking=True,
            deadline=date(2026, 7, 6),
            affected_user=None,
            replay_link=None,
            ticket_type=TicketType.FEATURE_REQUEST,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    assert result.ticket.type == TicketType.FEATURE_REQUEST
    assert result.ticket.priority == Priority.P2


@pytest.mark.asyncio
async def test_non_se_submitter_gets_confirmation_dm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A teammate who isn't the SE must get a receipt — otherwise the modal
    just closes and they assume nothing happened (Issue: 'only I can log')."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="Login broken",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_COLLEAGUE",
    )
    assert result.ticket is not None
    confirmations = [
        (user, text) for user, _blocks, text in fake_slack.dm_blocks_sent if user == "U_COLLEAGUE"
    ]
    assert len(confirmations) == 1
    assert result.ticket.display_id in confirmations[0][1]


@pytest.mark.asyncio
async def test_se_submitter_gets_no_duplicate_confirmation(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """When the SE logs their own ticket they already get the ack-draft DM, so
    we don't pile a redundant receipt on top."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="Login broken",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    # All SE DMs should be ack/override drafts — none should be the
    # "Ticket logged" receipt.
    receipts = [
        text
        for user, _blocks, text in fake_slack.dm_blocks_sent
        if text.startswith("Ticket logged")
    ]
    assert receipts == []


@pytest.mark.asyncio
async def test_csm_intake_blocking_yes_carries_impact(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_csm_intake(
        CSMIntakeSubmission(
            description="Salesforce sync broken since this morning",
            org_id="acme",
            prod_link="https://app.userled.io/...",
            blocking=True,
            deadline=date(2026, 6, 1),
            blocking_impact="Campaign launching Friday — exec escalating",
        ),
        reporter_user_id="U_CSM",
    )
    assert result.ticket is not None
    ticket = result.ticket
    assert ticket.id is not None
    assert ticket.severity == Severity.BLOCKING
    assert ticket.blocking_impact == "Campaign launching Friday — exec escalating"
    assert ticket.source == Source.TECH_ASSISTANCE
    assert ticket.deadline == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_submit_writes_status_change_event(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Step 5 — event log must record null → New."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    submit = _build(session_factory, fake_slack)
    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="x",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )

    # Verify the event was written by reading back via raw SQL — there's no
    # read API on the event-log repo by design, so this query uses SQLAlchemy
    # directly.
    from sqlalchemy import select

    from customerbot.data.database import EventStatusChangeRow

    async with session_factory() as session:
        result = await session.execute(select(EventStatusChangeRow))
        rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].from_status is None
    assert rows[0].to_status == TicketStatus.NEW.value


@pytest.mark.asyncio
async def test_submit_consumes_draft_session(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """When a slack_view_id matches a draft session, that draft is dropped."""
    from datetime import UTC, datetime, timedelta

    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    drafts = SQLiteDraftFormSessionRepository(session_factory)
    from customerbot.domain.bot_state.entities import DraftFormSession, ModalKind

    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    await drafts.create(
        DraftFormSession(
            slack_view_id="V_KEEP",
            modal_kind=ModalKind.SE_BUG,
            invoker_user_id="U_SE",
            created_at=now,
            expires_at=now + timedelta(minutes=30),
        )
    )

    submit = _build(session_factory, fake_slack)
    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="x",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
        slack_view_id="V_KEEP",
    )

    assert await drafts.get_by_view_id("V_KEEP") is None


@pytest.mark.asyncio
async def test_submit_skips_card_when_channel_unconfigured(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """SE_TICKETS_CHANNEL_ID=None → no card post, but ticket still created."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    submit = _build(session_factory, fake_slack, se_tickets_channel_id=None)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="x",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    assert result.ticket.id is not None
    assert fake_slack.blocks_posted == []
    assert result.card_message_ts is None


@pytest.mark.asyncio
async def test_unmapped_org_routes_to_unknown_catchall(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """An org_id with no seeded row falls back to the `unknown` catch-all:
    the ticket is bucketed under it and priced from its (high) weight."""
    orgs = SQLiteOrgRepository(session_factory)
    # Catch-all seeded as enterprise × negative × at-risk → CRITICAL weight.
    await orgs.upsert(
        Org(
            id="unknown",
            name="Unknown (unmapped customer)",
            acv_tier=ACVTier.ENTERPRISE,
            sentiment=Sentiment.NEGATIVE,
            renewal_status=RenewalStatus.AT_RISK,
        )
    )

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="brand-new-co",  # not in the table
            source=Source.IN_APP,
            summary="Filter dropdown won't open",
            description="desc",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    assert result.ticket.id is not None

    # Bucketed under the catch-all org, not the unrecognised raw id.
    tickets = SQLiteTicketRepository(session_factory)
    assert await tickets.list_orgs(result.ticket.id) == ["unknown"]

    # CRITICAL weight × BLOCKING severity → P1 (the top auto-assignable tier).
    assert result.ticket.priority == Priority.P1


@pytest.mark.asyncio
async def test_unmapped_org_without_catchall_is_unlinked(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """No `unknown` row → prior behaviour: ticket created but org-unlinked."""
    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="ghost-co",
            source=Source.DM,
            summary="x",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    assert result.ticket.id is not None
    tickets = SQLiteTicketRepository(session_factory)
    assert await tickets.list_orgs(result.ticket.id) == []


@pytest.mark.asyncio
async def test_create_org_from_intake_persists_and_slugs(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    submit = _build(session_factory, fake_slack)
    org_id = await submit.create_org_from_intake(
        name="Globex Inc.", channel_id="C0123ABCD", owner_id="U_OWNER"
    )
    assert org_id == "globex-inc"
    orgs = SQLiteOrgRepository(session_factory)
    created = await orgs.get(org_id)
    assert created is not None
    assert created.name == "Globex Inc."
    assert created.slack_channel_id == "C0123ABCD"
    assert created.csm_user_id == "U_OWNER"


@pytest.mark.asyncio
async def test_create_org_from_intake_dedupes_slug(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="globex", name="Globex"))
    submit = _build(session_factory, fake_slack)
    org_id = await submit.create_org_from_intake(
        name="Globex", channel_id="C999", owner_id="U_OWNER"
    )
    # Slug collision with the existing 'globex' → suffixed, not overwritten.
    assert org_id == "globex-2"
    original = await orgs.get("globex")
    assert original is not None and original.name == "Globex"  # untouched


@pytest.mark.asyncio
async def test_create_org_from_intake_rejects_duplicate_channel(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", slack_channel_id="C_ACME"))
    submit = _build(session_factory, fake_slack)
    with pytest.raises(OrgCreationError) as exc:
        await submit.create_org_from_intake(
            name="Impostor", channel_id="C_ACME", owner_id="U_OWNER"
        )
    assert exc.value.field == "channel"


@pytest.mark.asyncio
async def test_create_org_from_intake_requires_name_and_channel(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    submit = _build(session_factory, fake_slack)
    with pytest.raises(OrgCreationError) as name_err:
        await submit.create_org_from_intake(name="  ", channel_id="C1", owner_id="U")
    assert name_err.value.field == "name"
    with pytest.raises(OrgCreationError) as chan_err:
        await submit.create_org_from_intake(name="Acme", channel_id="", owner_id="U")
    assert chan_err.value.field == "channel"
    with pytest.raises(OrgCreationError) as fmt_err:
        await submit.create_org_from_intake(name="Acme", channel_id="notachannel", owner_id="U")
    assert fmt_err.value.field == "channel"


@pytest.mark.asyncio
async def test_se_bug_against_freshly_created_org_links_and_loops_csm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """End-to-end: create an org inline, then log a ticket against it."""
    submit = _build(session_factory, fake_slack)
    org_id = await submit.create_org_from_intake(name="Newco", channel_id="C_NEW", owner_id="U_CSM")
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id=org_id,
            source=Source.CUSTOMER_CHANNEL,
            summary="Broken thing",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None and result.ticket.id is not None
    tickets = SQLiteTicketRepository(session_factory)
    assert await tickets.list_orgs(result.ticket.id) == [org_id]
