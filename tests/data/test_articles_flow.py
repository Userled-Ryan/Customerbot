"""Integration tests for Chunk 12 — FAQ → article workflow.

Covers:
- `CreateArticleFromFAQ` writes an `articles` row in state `Suggested`,
  links it to the source FAQ ticket via `ticket_articles`, refreshes
  the ticket card, and DMs SE a confirmation.
- The button is FAQ-only — clicks on a Bug or Config ticket are
  rejected without writing anything.
- A FAQ ticket can close (`Resolved` button → `Awaiting customer`) and
  later `Closed` without waiting for the article to ship.
- `RenderArticlesBoard` groups articles by status with linked-ticket
  refs; empty board renders a single "no articles yet" block.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.ticket_card import ACTION_NEEDS_ARTICLE, build_blocks
from customerbot.application.tracking.articles import (
    CreateArticleFromFAQ,
    RenderArticlesBoard,
)
from customerbot.application.tracking.resolve import ResolveTicket
from customerbot.data.database import ArticleRow, TicketArticleRow
from customerbot.data.repository.articles import SQLiteArticleRepository
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Article, Ticket
from customerbot.domain.tickets.value_objects import (
    ArticleStatus,
    Lane,
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _faq(
    *,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    title: str = "How do I configure SAML?",
    card_channel_id: str | None = "C_SE_TICKETS",
    card_message_ts: str | None = "1700000000.000100",
) -> Ticket:
    return Ticket(
        title=title,
        type=TicketType.FAQ,
        subtype=TicketSubtype.NEEDS_ARTICLE,
        status=status,
        lane=None,
        priority=Priority.P3,
        reporter_user_id="U_SE",
        source=Source.TECH_ASSISTANCE,
        description="customer asked about SSO setup",
        card_channel_id=card_channel_id,
        card_message_ts=card_message_ts,
        created_at=datetime(2026, 6, 1, 9, 0),
    )


def _bug(**kwargs: object) -> Ticket:
    """Bug ticket — confirms the FAQ button only renders for FAQ tickets."""
    defaults: dict[str, object] = {
        "title": "checkout broken",
        "type": TicketType.BUG,
        "subtype": TicketSubtype.PLATFORM_WIDE,
        "status": TicketStatus.IN_PROGRESS,
        "lane": Lane.SE_ACTION,
        "priority": Priority.P2,
        "reporter_user_id": "U_SE",
        "source": Source.CUSTOMER_CHANNEL,
        "description": "users hang on submit",
        "created_at": datetime(2026, 6, 1, 9, 0),
    }
    defaults.update(kwargs)
    return Ticket(**defaults)  # type: ignore[arg-type]


# --- Ticket-card button rendering --------------------------------------------


def test_faq_card_renders_needs_article_button() -> None:
    ticket = _faq()
    ticket.id = 7
    blocks = build_blocks(ticket, [])
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    # Two action rows: the standard six buttons, plus the secondary row
    # which carries Set-deadline + (FAQ-only) Needs-article.
    assert len(action_blocks) == 2
    second_row_action_ids = {el["action_id"] for el in action_blocks[1]["elements"]}
    assert ACTION_NEEDS_ARTICLE in second_row_action_ids
    needs_article_btn = next(
        el for el in action_blocks[1]["elements"] if el["action_id"] == ACTION_NEEDS_ARTICLE
    )
    assert needs_article_btn["value"] == "7"


def test_non_faq_card_omits_needs_article_button() -> None:
    ticket = _bug()
    ticket.id = 7
    blocks = build_blocks(ticket, [])
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    # Secondary row still renders for the Set-deadline button on non-FAQ tickets;
    # `Needs article` is what's FAQ-gated.
    all_action_ids = {el["action_id"] for row in action_blocks for el in row["elements"]}
    assert ACTION_NEEDS_ARTICLE not in all_action_ids


# --- CreateArticleFromFAQ ----------------------------------------------------


@pytest.mark.asyncio
async def test_needs_article_click_creates_suggested_article_linked_to_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    articles = SQLiteArticleRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    created = await tickets.create(_faq())
    assert created.id is not None

    use_case = CreateArticleFromFAQ(
        tickets=tickets,
        articles=articles,
        orgs=orgs,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    article = await use_case.execute(ticket_id=created.id, by_user_id="U_SE")
    assert article is not None
    assert article.id is not None
    assert article.status == ArticleStatus.SUGGESTED
    assert article.title == created.title
    assert article.owner_user_id == "U_SE"

    # ticket_articles row exists.
    async with session_factory() as session:
        rows = list((await session.execute(select(TicketArticleRow))).scalars())
    assert len(rows) == 1
    assert rows[0].ticket_id == created.id
    assert rows[0].article_id == article.id

    # list_linked_tickets round-trips.
    assert await articles.list_linked_tickets(article.id) == [created.id]

    # Card refreshed + SE confirmation DM.
    assert any(ch == "C_SE_TICKETS" for ch, _, _, _ in fake_slack.messages_updated)
    assert any(user == "U_SE" for user, _ in fake_slack.dms_sent)


@pytest.mark.asyncio
async def test_needs_article_click_rejected_on_non_faq_ticket(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    articles = SQLiteArticleRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    bug = await tickets.create(_bug())
    assert bug.id is not None

    use_case = CreateArticleFromFAQ(
        tickets=tickets,
        articles=articles,
        orgs=orgs,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    result = await use_case.execute(ticket_id=bug.id, by_user_id="U_SE")
    assert result is None
    # No article row written.
    async with session_factory() as session:
        rows = list((await session.execute(select(ArticleRow))).scalars())
    assert rows == []


@pytest.mark.asyncio
async def test_needs_article_click_on_missing_ticket_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    articles = SQLiteArticleRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    use_case = CreateArticleFromFAQ(
        tickets=tickets,
        articles=articles,
        orgs=orgs,
        slack=fake_slack,
        se_user_id="U_SE",
    )
    assert await use_case.execute(ticket_id=999, by_user_id="U_SE") is None
    assert fake_slack.dms_sent == []


# --- FAQ closes without article ----------------------------------------------


@pytest.mark.asyncio
async def test_faq_ticket_can_close_independently_of_article(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    articles = SQLiteArticleRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    faq_ticket = await tickets.create(_faq())
    assert faq_ticket.id is not None

    # 1) SE clicks Needs article — article queued in Suggested.
    create_article = CreateArticleFromFAQ(
        tickets=tickets, articles=articles, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    article = await create_article.execute(ticket_id=faq_ticket.id, by_user_id="U_SE")
    assert article is not None and article.id is not None
    # Article stays in Suggested.
    assert article.status == ArticleStatus.SUGGESTED

    # 2) SE then clicks Resolved on the FAQ ticket — moves to awaiting customer.
    resolve = ResolveTicket(
        tickets=tickets, events=events, orgs=orgs, slack=fake_slack, se_user_id="U_SE"
    )
    result = await resolve.execute(ticket_id=faq_ticket.id, by_user_id="U_SE", via_hotfix=False)
    assert result.ticket is not None
    assert result.ticket.status == TicketStatus.AWAITING_CUSTOMER

    # Article is untouched — still Suggested, still linked.
    refreshed_article = await articles.get(article.id)
    assert refreshed_article is not None
    assert refreshed_article.status == ArticleStatus.SUGGESTED
    assert await articles.list_linked_tickets(article.id) == [faq_ticket.id]


# --- RenderArticlesBoard -----------------------------------------------------


@pytest.mark.asyncio
async def test_articles_board_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    articles = SQLiteArticleRepository(session_factory)
    board = RenderArticlesBoard(articles=articles, tickets=tickets)
    blocks = await board.execute()
    assert len(blocks) == 1
    assert "no articles yet" in blocks[0]["text"]["text"]


@pytest.mark.asyncio
async def test_articles_board_groups_by_status_with_linked_tickets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    articles = SQLiteArticleRepository(session_factory)
    faq_a = await tickets.create(_faq(title="SAML setup"))
    faq_b = await tickets.create(_faq(title="OAuth scopes"))
    assert faq_a.id is not None and faq_b.id is not None
    # Two suggested articles linked to the two FAQ tickets, one live article
    # for variety.
    art1 = await articles.create(
        Article(title="SAML setup", status=ArticleStatus.SUGGESTED, owner_user_id="U_SE")
    )
    assert art1.id is not None
    await articles.link_to_ticket(art1.id, faq_a.id)
    art2 = await articles.create(
        Article(title="OAuth scopes", status=ArticleStatus.SUGGESTED, owner_user_id="U_SE")
    )
    assert art2.id is not None
    await articles.link_to_ticket(art2.id, faq_b.id)
    art3 = await articles.create(
        Article(title="Single sign-on overview", status=ArticleStatus.LIVE, owner_user_id="U_SE")
    )
    assert art3.id is not None

    board = RenderArticlesBoard(articles=articles, tickets=tickets)
    blocks = await board.execute()
    rendered = "\n".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "section"
    )
    # Both suggested articles surface with their linked TIC ids.
    assert "Suggested" in rendered
    assert "Live" in rendered
    assert "SAML setup" in rendered
    assert "OAuth scopes" in rendered
    assert "Single sign-on overview" in rendered
    assert faq_a.display_id in rendered
    assert faq_b.display_id in rendered
    # Live article has no linked tickets — shows the placeholder.
    assert "no linked tickets" in rendered
