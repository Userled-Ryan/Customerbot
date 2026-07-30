from __future__ import annotations

from collections.abc import Collection
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
from customerbot.application.linear.sync import LinearSync
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
from tests.conftest import FakeLinearPort, FakeSlackPort


def _build(
    factory: async_sessionmaker[AsyncSession],
    slack: FakeSlackPort,
    *,
    se_tickets_channel_id: str | None = "C_SE_TICKETS",
    se_owner_user_ids: Collection[str] = (),
    linear: LinearSync | None = None,
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
        se_owner_user_ids=se_owner_user_ids,
        se_tickets_channel_id=se_tickets_channel_id,
        linear=linear,
    )


def _se_bug(summary: str = "Publishing fails on iOS", org_id: str = "acme") -> SEBugSubmission:
    return SEBugSubmission(
        org_id=org_id,
        source=Source.CUSTOMER_CHANNEL,
        summary=summary,
        description="Detailed description.",
        blocking=False,
        deadline=None,
        affected_user=None,
        replay_link=None,
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
async def test_new_ticket_defaults_se_owner_to_se_user(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Every ticket's SE owner defaults to the configured SE on creation — not
    exposed to the logger, reassigned later from the card dropdown."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Publishing fails on iOS",
            description="Detailed description.",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_OTHER",
        slack_view_id="V_TEST",
        original_slack_link="https://slack/p999",
    )
    assert result.ticket is not None
    assert result.ticket.id is not None
    # Owner defaults to the SE even though the reporter was someone else.
    assert result.ticket.se_owner_user_id == "U_SE"
    tickets = SQLiteTicketRepository(session_factory)
    persisted = await tickets.get(result.ticket.id)
    assert persisted is not None and persisted.se_owner_user_id == "U_SE"


@pytest.mark.asyncio
async def test_round_robin_default_owner_balances_by_open_load(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """With a multi-member pool, a new ticket's default owner is the SE with the
    fewest open tickets; ties break by pool order."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    # Skew the load: U_SE already owns one open ticket, U_ELIZA owns none.
    await tickets.create(
        Ticket(
            title="Existing",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            se_owner_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            description="",
        )
    )

    submit = _build(session_factory, fake_slack, se_owner_user_ids=["U_SE", "U_ELIZA"])

    # First new ticket → U_ELIZA (0 open) beats U_SE (1 open).
    first = await submit.from_se_bug(
        _se_bug("Export button greyed out"),
        reporter_user_id="U_OTHER",
        slack_view_id="V1",
        original_slack_link="s1",
    )
    assert first.ticket is not None and first.ticket.se_owner_user_id == "U_ELIZA"

    # Now both own 1 open ticket → tie broken by pool order → U_SE.
    second = await submit.from_se_bug(
        _se_bug("Login redirect loops on Safari"),
        reporter_user_id="U_OTHER",
        slack_view_id="V2",
        original_slack_link="s2",
    )
    assert second.ticket is not None and second.ticket.se_owner_user_id == "U_SE"


@pytest.mark.asyncio
async def test_round_robin_prefers_linear_active_load(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """When Linear can answer, the picker balances by the live Linear active-issue
    count — even when it disagrees with the local open-ticket count."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    # Local count would favour U_ELIZA (U_SE owns one open ticket, U_ELIZA none)...
    await tickets.create(
        Ticket(
            title="Existing",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            se_owner_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            description="",
        )
    )
    # ...but Linear says U_ELIZA carries the heavier active load, so U_SE wins.
    fake_linear = FakeLinearPort(se_load={"U_SE": 1, "U_ELIZA": 4})
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    submit = _build(session_factory, fake_slack, se_owner_user_ids=["U_SE", "U_ELIZA"], linear=sync)

    result = await submit.from_se_bug(
        _se_bug("Export button greyed out"),
        reporter_user_id="U_OTHER",
        slack_view_id="V1",
        original_slack_link="s1",
    )
    assert result.ticket is not None and result.ticket.se_owner_user_id == "U_SE"


@pytest.mark.asyncio
async def test_round_robin_falls_back_to_local_when_linear_declines(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """When Linear can't answer (unreachable / an SE unmapped → count returns
    None), the picker falls back to the local open-ticket count."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(
        Ticket(
            title="Existing",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            se_owner_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            description="",
        )
    )
    # se_load defaults to None → count_active_se_load returns None → local path.
    fake_linear = FakeLinearPort()
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    submit = _build(session_factory, fake_slack, se_owner_user_ids=["U_SE", "U_ELIZA"], linear=sync)

    result = await submit.from_se_bug(
        _se_bug("Export button greyed out"),
        reporter_user_id="U_OTHER",
        slack_view_id="V1",
        original_slack_link="s1",
    )
    # U_ELIZA (0 local open) beats U_SE (1 local open).
    assert result.ticket is not None and result.ticket.se_owner_user_id == "U_ELIZA"


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
async def test_csm_help_ticket_always_urgent_and_unassigned(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A CSM Help Request is always urgent (P1, no deadline, SE lane) regardless
    of the urgent flag, carries the single CSM_ASSISTANCE subtype, and stays
    unassigned — round-robin is skipped so someone claims it from the card."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    # A populated SE pool would normally round-robin an owner; CSM Help opts out.
    submit = _build(session_factory, fake_slack, se_owner_user_ids=["U_SE", "U_ELIZA"])
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="Need a hand building the QBR deck for Acme",
            description="Out this week — can someone pick up the deck build?",
            blocking=False,
            deadline=date(2026, 8, 3),  # dropped: urgent has no deadline
            affected_user=None,
            replay_link=None,
            ticket_type=TicketType.CSM_HELP,
            urgent=False,  # forced urgent regardless of the checkbox
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    ticket = result.ticket
    assert ticket.type == TicketType.CSM_HELP
    assert ticket.subtype == TicketSubtype.CSM_ASSISTANCE
    assert ticket.urgent is True
    assert ticket.is_urgent is True  # urgent + still NEW → rides the hourly nag
    assert ticket.priority == Priority.P1
    assert ticket.deadline is None
    assert ticket.lane == Lane.SE_ACTION
    assert ticket.se_owner_user_id is None  # unassigned — claimed from the card

    # Stays unassigned after the round-trip too.
    tickets = SQLiteTicketRepository(session_factory)
    persisted = await tickets.get(ticket.id or 0)
    assert persisted is not None and persisted.se_owner_user_id is None


@pytest.mark.asyncio
async def test_urgent_bug_forced_p1_no_deadline_and_se_owned(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """An urgent ticket is forced to P1, drops any deadline, rides the SE lane,
    and is assigned to the configured SE — regardless of the customer weight."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Everything is on fire",
            description="",
            blocking=True,
            deadline=date(2026, 12, 1),  # even a real deadline is dropped
            affected_user=None,
            replay_link=None,
            urgent=True,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    ticket = result.ticket
    assert ticket.urgent is True
    assert ticket.is_urgent is True  # urgent + still NEW
    assert ticket.priority == Priority.P1
    assert ticket.deadline is None
    assert ticket.lane == Lane.SE_ACTION
    assert ticket.se_owner_user_id == "U_SE"

    # Persisted urgent flag survives the round-trip.
    tickets = SQLiteTicketRepository(session_factory)
    persisted = await tickets.get(ticket.id or 0)
    assert persisted is not None and persisted.urgent is True


@pytest.mark.asyncio
async def test_urgent_ticket_dms_owner_immediately(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Urgent tickets alert the SE owner at once, not just on the next hourly nag."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    result = await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.CUSTOMER_CHANNEL,
            summary="Everything is on fire",
            description="",
            blocking=True,
            deadline=None,
            affected_user=None,
            replay_link=None,
            urgent=True,
        ),
        reporter_user_id="U_SE",
    )
    assert result.ticket is not None
    urgent_dms = [
        (user, text)
        for user, _blocks, text in fake_slack.dm_blocks_sent
        if user == "U_SE" and text.startswith(":rotating_light: Urgent ticket logged")
    ]
    assert len(urgent_dms) == 1
    assert result.ticket.display_id in urgent_dms[0][1]


@pytest.mark.asyncio
async def test_non_urgent_ticket_sends_no_urgent_dm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A normal ticket must not trigger the urgent-owner alert."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))

    submit = _build(session_factory, fake_slack)
    await submit.from_se_bug(
        SEBugSubmission(
            org_id="acme",
            source=Source.DM,
            summary="Minor thing",
            description="",
            blocking=False,
            deadline=None,
            affected_user=None,
            replay_link=None,
        ),
        reporter_user_id="U_SE",
    )
    assert not any(
        text.startswith(":rotating_light: Urgent ticket logged")
        for _user, _blocks, text in fake_slack.dm_blocks_sent
    )


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
