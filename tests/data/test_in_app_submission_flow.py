"""Integration test for SubmitTicketForm.from_in_app_webhook (Chunk 14).

Drives a real `SubmitTicketForm` through the SQLite repos and asserts:
- Ticket persisted with Source.IN_APP, screenshot/replay/page URL set
- Org linked via `ticket_orgs`
- §9a draft DM and ticket card both fire as for other intake paths
- Feed entry posted to `#tech-assistance` for visibility (§3d)
- Dedupe path: when a candidate matches, no ticket is created and the
  pending row is returned so the webhook can surface a different response
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.dedupe import (
    FindDedupeCandidate,
    OfferDedupeChoice,
)
from customerbot.application.intake.submissions import InAppBugSubmission
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import load_or_default
from customerbot.data.repository.bot_state import (
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _payload() -> InAppBugSubmission:
    return InAppBugSubmission(
        org_id="acme",
        user_id="U_CUSTOMER",
        user_email="user@acme.io",
        page_url="https://app.userled.io/campaigns/42",
        description="Filter dropdown won't open on the campaign page",
        screenshot_url="https://cdn.userled.io/x.png",
        session_replay_url="https://replay.userled.io/abc",
    )


def _build_submit(
    session_factory: async_sessionmaker[AsyncSession],
    slack: FakeSlackPort,
    *,
    tech_assistance_channel_id: str | None = "C_TECH_ASSIST",
    se_tickets_channel_id: str | None = "C_SE_TICKETS",
) -> SubmitTicketForm:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    drafts = SQLiteDraftFormSessionRepository(session_factory)
    pending_dedupe = SQLitePendingDedupeChoiceRepository(session_factory)
    find_dedupe = FindDedupeCandidate(tickets=tickets)
    offer_dedupe = OfferDedupeChoice(slack=slack, pending=pending_dedupe)
    assign_priority = AssignPriority(matrix=load_or_default(None), events=events, slack=slack)
    return SubmitTicketForm(
        slack=slack,
        tickets=tickets,
        events=events,
        orgs=orgs,
        drafts=drafts,
        find_dedupe=find_dedupe,
        offer_dedupe=offer_dedupe,
        assign_priority=assign_priority,
        se_user_id="U_SE",
        se_tickets_channel_id=se_tickets_channel_id,
        tech_assistance_channel_id=tech_assistance_channel_id,
    )


@pytest.mark.asyncio
async def test_in_app_submission_creates_ticket_and_posts_feed_entry(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    submit = _build_submit(session_factory, fake_slack)

    result = await submit.from_in_app_webhook(_payload())
    assert result.ticket is not None
    ticket = result.ticket
    assert ticket.id is not None
    # Source is IN_APP and the structured fields land where they should.
    assert ticket.source == Source.IN_APP
    assert ticket.prod_link == "https://app.userled.io/campaigns/42"
    assert ticket.screenshot_url == "https://cdn.userled.io/x.png"
    assert ticket.replay_link == "https://replay.userled.io/abc"
    assert ticket.affected_user == "user@acme.io"
    assert ticket.severity == Severity.UNSURE
    # Description carries the in-app context note SE will see inline.
    assert "In-app submission" in ticket.description
    assert "user@acme.io" in ticket.description
    # Org linked.
    assert await tickets.list_orgs(ticket.id) == ["acme"]
    # Standard intake side-effects: SE DM (§9a draft) + ticket card.
    assert any(user == "U_SE" for user, _blocks, _text in fake_slack.dm_blocks_sent)
    se_cards = [p for p in fake_slack.blocks_posted if p[0] == "C_SE_TICKETS"]
    assert len(se_cards) == 1
    # §3d feed entry to #tech-assistance.
    feed_entries = [p for p in fake_slack.blocks_posted if p[0] == "C_TECH_ASSIST"]
    assert len(feed_entries) == 1
    _ch, blocks, _text = feed_entries[0]
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    assert "Acme Corp" in rendered
    assert "in-app bug submitted" in rendered.lower()


@pytest.mark.asyncio
async def test_in_app_submission_with_dedupe_match_does_not_create_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    # Seed an existing live ticket the new submission will match on prod_link.
    existing = await tickets.create(
        Ticket(
            title="Filter dropdown won't open on the campaign page",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            status=TicketStatus.IN_PROGRESS,
            lane=Lane.SE_ACTION,
            priority=Priority.P3,
            severity=Severity.DEGRADED,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            description="dup",
            prod_link="https://app.userled.io/campaigns/42",
        )
    )
    assert existing.id is not None
    await tickets.add_org(existing.id, "acme")

    submit = _build_submit(session_factory, fake_slack)
    result = await submit.from_in_app_webhook(_payload())
    # Dedupe match → no ticket created, pending row carries the choice.
    assert result.ticket is None
    assert result.pending_dedupe is not None
    # SE got the Merge / Create-new DM. No tech-assistance feed entry on the
    # dedupe path — we only feed when a fresh ticket was created.
    assert any(user == "U_SE" for user, _blocks, _text in fake_slack.dm_blocks_sent)
    assert [p for p in fake_slack.blocks_posted if p[0] == "C_TECH_ASSIST"] == []


@pytest.mark.asyncio
async def test_in_app_submission_skips_feed_when_channel_unconfigured(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme Corp"))
    submit = _build_submit(session_factory, fake_slack, tech_assistance_channel_id=None)
    result = await submit.from_in_app_webhook(_payload())
    assert result.ticket is not None
    # No feed entry posted, but the ticket card still went out.
    assert all(p[0] != "C_TECH_ASSIST" for p in fake_slack.blocks_posted)
    assert any(p[0] == "C_SE_TICKETS" for p in fake_slack.blocks_posted)
