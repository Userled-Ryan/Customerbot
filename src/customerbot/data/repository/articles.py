from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import ArticleRow, TicketArticleRow
from customerbot.domain.tickets.entities import Article
from customerbot.domain.tickets.value_objects import ArticleStatus

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _str_to_dt(s: str) -> datetime:
    return datetime.strptime(s, _DT_FMT)


def _row_to_article(row: ArticleRow) -> Article:
    return Article(
        id=row.id,
        title=row.title,
        status=ArticleStatus(row.status),
        owner_user_id=row.owner_user_id,
        url=row.url,
        created_at=_str_to_dt(row.created_at),
        published_at=_str_to_dt(row.published_at) if row.published_at else None,
        updated_at=_str_to_dt(row.updated_at),
    )


class SQLiteArticleRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, article: Article) -> Article:
        async with self._session_factory() as session:
            row = ArticleRow(
                title=article.title,
                status=article.status.value,
                owner_user_id=article.owner_user_id,
                url=article.url,
                created_at=_dt_to_str(article.created_at),
                published_at=_dt_to_str(article.published_at) if article.published_at else None,
                updated_at=_dt_to_str(article.updated_at),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_article(row)

    async def get(self, article_id: int) -> Article | None:
        async with self._session_factory() as session:
            row = await session.get(ArticleRow, article_id)
            return _row_to_article(row) if row else None

    async def link_to_ticket(self, article_id: int, ticket_id: int) -> None:
        async with self._session_factory() as session:
            stmt = (
                insert(TicketArticleRow)
                .values(ticket_id=ticket_id, article_id=article_id)
                .on_conflict_do_nothing(index_elements=["ticket_id", "article_id"])
            )
            await session.execute(stmt)
            await session.commit()

    async def list_all(self) -> list[Article]:
        async with self._session_factory() as session:
            result = await session.execute(select(ArticleRow).order_by(ArticleRow.id.desc()))
            return [_row_to_article(r) for r in result.scalars().all()]
