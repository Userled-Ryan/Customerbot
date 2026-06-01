from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.repository.articles import SQLiteArticleRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Article, Ticket
from customerbot.domain.tickets.value_objects import (
    ArticleStatus,
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)


def _faq_ticket() -> Ticket:
    return Ticket(
        title="How do I X?",
        type=TicketType.FAQ,
        subtype=TicketSubtype.NEEDS_ARTICLE,
        severity=Severity.UNSURE,
        reporter_user_id="U_CSM",
        source=Source.TECH_ASSISTANCE,
    )


@pytest.mark.asyncio
async def test_create_and_get_article(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteArticleRepository(session_factory)
    created = await repo.create(Article(title="How to publish a campaign"))

    assert created.id is not None
    assert created.status == ArticleStatus.SUGGESTED

    got = await repo.get(created.id)
    assert got is not None
    assert got.title == "How to publish a campaign"


@pytest.mark.asyncio
async def test_link_article_to_ticket(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    articles = SQLiteArticleRepository(session_factory)

    t = await tickets.create(_faq_ticket())
    a = await articles.create(Article(title="Foo"))
    assert t.id and a.id

    await articles.link_to_ticket(a.id, t.id)
    # Idempotent
    await articles.link_to_ticket(a.id, t.id)
