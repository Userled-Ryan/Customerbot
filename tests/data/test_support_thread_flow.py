"""Support-thread reactions + resolved reply, end-to-end against real SQLite.

Covers the three attach points that mark a #userled-support thread in flight
(🎫) and the resolve fan-out that swaps them to ✅ with a threaded reply.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.dedupe import (
    FindDedupeCandidate,
    MergeIntoExisting,
    OfferDedupeChoice,
)
from customerbot.application.intake.submissions import SEBugSubmission
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.application.intake.support_threads import (
    IN_FLIGHT_REACTION,
    RESOLVED_REACTION,
    RESOLVED_THREAD_REPLY,
)
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import PriorityMatrix
from customerbot.application.tracking.resolve import ResolveTicket
from customerbot.data.repository.bot_state import (
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    ResolutionType,
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort

SUPPORT_CHANNEL = "C_SUPPORT"
GLEAP_CHANNEL = "C_GLEAP"
SUPPORT_CHANNELS = (SUPPORT_CHANNEL, GLEAP_CHANNEL)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _build_submit(
    factory: async_sessionmaker[AsyncSession],
    slack: FakeSlackPort,
    support_channels: tuple[str, ...] = SUPPORT_CHANNELS,
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
        se_tickets_channel_id="C_SE_TICKETS",
        tech_assistance_channel_id=SUPPORT_CHANNEL,
        support_channel_ids=support_channels,
    )


def _build_resolve(
    factory: async_sessionmaker[AsyncSession],
    slack: FakeSlackPort,
    support_channels: tuple[str, ...] = SUPPORT_CHANNELS,
) -> ResolveTicket:
    return ResolveTicket(
        tickets=SQLiteTicketRepository(factory),
        events=SQLiteEventLogRepository(factory),
        orgs=SQLiteOrgRepository(factory),
        slack=slack,
        se_user_id="U_SE",
        support_channel_ids=support_channels,
    )


async def _seed_org(factory: async_sessionmaker[AsyncSession], org_id: str) -> None:
    await SQLiteOrgRepository(factory).upsert(Org(id=org_id, name=org_id.upper()))


def _submission(summary: str, description: str) -> SEBugSubmission:
    return SEBugSubmission(
        org_id="acme",
        source=Source.CUSTOMER_CHANNEL,
        summary=summary,
        description=description,
        blocking=True,
        deadline=None,
        affected_user=None,
        replay_link=None,
    )


def _support_link(slack: FakeSlackPort, thread_ts: str, channel: str = SUPPORT_CHANNEL) -> str:
    return slack.build_thread_link(channel, thread_ts)


@pytest.mark.asyncio
async def test_create_from_support_thread_adds_ticket_reaction(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)

    result = await submit.from_se_bug(
        _submission("Publishing fails", "Cannot publish on Safari"),
        reporter_user_id="U_SE",
        original_slack_link=_support_link(fake_slack, "100.000001"),
    )
    assert result.ticket is not None

    assert fake_slack.reactions_added == [(SUPPORT_CHANNEL, "100.000001", IN_FLIGHT_REACTION)]
    tickets = SQLiteTicketRepository(session_factory)
    assert await tickets.list_support_threads(result.ticket.id or 0) == [
        (SUPPORT_CHANNEL, "100.000001")
    ]


@pytest.mark.asyncio
async def test_create_from_other_channel_adds_no_reaction(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)

    result = await submit.from_se_bug(
        _submission("Publishing fails", "Cannot publish on Safari"),
        reporter_user_id="U_SE",
        original_slack_link=_support_link(fake_slack, "100.000001", channel="C_OTHER"),
    )
    assert result.ticket is not None
    assert fake_slack.reactions_added == []
    tickets = SQLiteTicketRepository(session_factory)
    assert await tickets.list_support_threads(result.ticket.id or 0) == []


@pytest.mark.asyncio
async def test_gleap_channel_thread_gets_status_loop_end_to_end(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A ticket logged from the Gleap channel joins the same 🎫→✅ loop as
    #userled-support: 🎫 on creation, then reply + ✅ on resolve."""
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)

    result = await submit.from_se_bug(
        _submission("Dropdown broken", "Filter won't open"),
        reporter_user_id="U_SE",
        original_slack_link=_support_link(fake_slack, "300.000003", channel=GLEAP_CHANNEL),
    )
    assert result.ticket is not None and result.ticket.id is not None
    assert fake_slack.reactions_added == [(GLEAP_CHANNEL, "300.000003", IN_FLIGHT_REACTION)]
    tickets = SQLiteTicketRepository(session_factory)
    assert await tickets.list_support_threads(result.ticket.id) == [(GLEAP_CHANNEL, "300.000003")]

    resolve = _build_resolve(session_factory, fake_slack)
    await resolve.execute(
        ticket_id=result.ticket.id,
        by_user_id="U_SE",
        resolution_type=ResolutionType.NO_CODE_CHANGE,
        sync_to_linear=False,
    )
    replies = [
        (ch, ts) for ch, text, ts in fake_slack.messages_sent if text == RESOLVED_THREAD_REPLY
    ]
    assert (GLEAP_CHANNEL, "300.000003") in replies
    assert (GLEAP_CHANNEL, "300.000003", IN_FLIGHT_REACTION) in fake_slack.reactions_removed
    assert (GLEAP_CHANNEL, "300.000003", RESOLVED_REACTION) in fake_slack.reactions_added


@pytest.mark.asyncio
async def test_create_via_slash_command_no_link_adds_no_reaction(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)

    result = await submit.from_se_bug(
        _submission("Publishing fails", "Cannot publish on Safari"),
        reporter_user_id="U_SE",
        original_slack_link=None,
    )
    assert result.ticket is not None
    assert fake_slack.reactions_added == []


@pytest.mark.asyncio
async def test_merge_links_and_reacts_merged_thread(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    await _seed_org(session_factory, "acme")
    submit = _build_submit(session_factory, fake_slack)
    tickets = SQLiteTicketRepository(session_factory)
    pending_repo = SQLitePendingDedupeChoiceRepository(session_factory)

    first = await submit.from_se_bug(
        _submission("Publishing fails on Safari", "iOS Safari publishing broken on editor"),
        reporter_user_id="U_SE",
        original_slack_link=_support_link(fake_slack, "100.000001"),
    )
    assert first.ticket is not None
    # Second, very similar → dedupe pending; its own support thread.
    second = await submit.from_se_bug(
        _submission(
            "Publishing fails on Safari iOS", "iOS Safari publishing broken on editor repro"
        ),
        reporter_user_id="U_SE",
        original_slack_link=_support_link(fake_slack, "200.000002"),
    )
    assert second.pending_dedupe is not None and second.pending_dedupe.id is not None

    merge = MergeIntoExisting(
        tickets=tickets,
        events=SQLiteEventLogRepository(session_factory),
        orgs=SQLiteOrgRepository(session_factory),
        pending=pending_repo,
        slack=fake_slack,
        se_tickets_channel_id="C_SE_TICKETS",
        support_channel_ids=SUPPORT_CHANNELS,
    )
    fake_slack.reactions_added.clear()
    await merge.execute(pending_id=second.pending_dedupe.id, by_user_id="U_SE")

    # Merged thread is attached to the surviving ticket and reacted 🎫.
    assert (SUPPORT_CHANNEL, "200.000002", IN_FLIGHT_REACTION) in fake_slack.reactions_added
    threads = await tickets.list_support_threads(first.ticket.id or 0)
    assert set(threads) == {(SUPPORT_CHANNEL, "100.000001"), (SUPPORT_CHANNEL, "200.000002")}


@pytest.mark.asyncio
async def test_resolve_replies_and_swaps_all_threads(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    t = await tickets.create(
        Ticket(
            title="x",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
        )
    )
    assert t.id is not None
    now = _utcnow()
    await tickets.link_support_thread(t.id, SUPPORT_CHANNEL, "100.1", by_user_id="U_SE", now=now)
    await tickets.link_support_thread(t.id, SUPPORT_CHANNEL, "200.2", by_user_id="U_SE", now=now)

    resolve = _build_resolve(session_factory, fake_slack)
    await resolve.execute(
        ticket_id=t.id,
        by_user_id="U_SE",
        resolution_type=ResolutionType.NO_CODE_CHANGE,
        sync_to_linear=False,
    )

    replies = [
        (ch, ts) for ch, text, ts in fake_slack.messages_sent if text == RESOLVED_THREAD_REPLY
    ]
    assert set(replies) == {(SUPPORT_CHANNEL, "100.1"), (SUPPORT_CHANNEL, "200.2")}
    assert (SUPPORT_CHANNEL, "100.1", IN_FLIGHT_REACTION) in fake_slack.reactions_removed
    assert (SUPPORT_CHANNEL, "200.2", IN_FLIGHT_REACTION) in fake_slack.reactions_removed
    assert (SUPPORT_CHANNEL, "100.1", RESOLVED_REACTION) in fake_slack.reactions_added
    assert (SUPPORT_CHANNEL, "200.2", RESOLVED_REACTION) in fake_slack.reactions_added


@pytest.mark.asyncio
async def test_resolve_falls_back_to_original_slack_link(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Tickets created before this feature have no rows — use original_slack_link."""
    tickets = SQLiteTicketRepository(session_factory)
    t = await tickets.create(
        Ticket(
            title="x",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            original_slack_link=fake_slack.build_thread_link(SUPPORT_CHANNEL, "300.000003"),
        )
    )
    assert t.id is not None

    resolve = _build_resolve(session_factory, fake_slack)
    await resolve.execute(
        ticket_id=t.id,
        by_user_id="U_SE",
        resolution_type=ResolutionType.NO_CODE_CHANGE,
        sync_to_linear=False,
    )

    replies = [
        (ch, ts) for ch, text, ts in fake_slack.messages_sent if text == RESOLVED_THREAD_REPLY
    ]
    assert replies == [(SUPPORT_CHANNEL, "300.000003")]


@pytest.mark.asyncio
async def test_resolve_twice_does_not_double_reply(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    t = await tickets.create(
        Ticket(
            title="x",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
        )
    )
    assert t.id is not None
    await tickets.link_support_thread(
        t.id, SUPPORT_CHANNEL, "100.1", by_user_id="U_SE", now=_utcnow()
    )

    resolve = _build_resolve(session_factory, fake_slack)
    for _ in range(2):
        await resolve.execute(
            ticket_id=t.id,
            by_user_id="U_SE",
            resolution_type=ResolutionType.NO_CODE_CHANGE,
            sync_to_linear=False,
        )

    replies = [text for _ch, text, _ts in fake_slack.messages_sent if text == RESOLVED_THREAD_REPLY]
    assert len(replies) == 1
