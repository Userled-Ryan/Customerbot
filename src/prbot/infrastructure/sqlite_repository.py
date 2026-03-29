from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import MessageRef, PRUrl
from prbot.infrastructure.database import TrackedPRRow


class SQLitePRRepository:
    """Concrete adapter: stores tracked PRs in SQLite via SQLAlchemy async."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, tracked_pr: TrackedPR) -> None:
        async with self._session_factory() as session:
            stmt = (
                insert(TrackedPRRow)
                .values(
                    owner=tracked_pr.pr_url.owner,
                    repo=tracked_pr.pr_url.repo,
                    pr_number=tracked_pr.pr_url.number,
                    integration_id=tracked_pr.message_ref.integration_id,
                    message_ref=tracked_pr.message_ref.ref,
                    applied_emojis=_serialize_emojis(tracked_pr.applied_emojis),
                )
                .on_conflict_do_update(
                    index_elements=[
                        "owner",
                        "repo",
                        "pr_number",
                        "integration_id",
                        "message_ref",
                    ],
                    set_={
                        "applied_emojis": _serialize_emojis(tracked_pr.applied_emojis),
                        "updated_at": func.current_timestamp(),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]:
        async with self._session_factory() as session:
            stmt = select(TrackedPRRow).where(
                TrackedPRRow.owner == pr_url.owner,
                TrackedPRRow.repo == pr_url.repo,
                TrackedPRRow.pr_number == pr_url.number,
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_entity(row) for row in rows]

    async def add_emoji(
        self,
        pr_url: PRUrl,
        message_ref: MessageRef,
        emoji: str,
    ) -> None:
        async with self._session_factory() as session:
            stmt = select(TrackedPRRow.applied_emojis).where(
                TrackedPRRow.owner == pr_url.owner,
                TrackedPRRow.repo == pr_url.repo,
                TrackedPRRow.pr_number == pr_url.number,
                TrackedPRRow.integration_id == message_ref.integration_id,
                TrackedPRRow.message_ref == message_ref.ref,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return

            current = _deserialize_emojis(row)
            updated = current | {emoji}

            update_stmt = (
                update(TrackedPRRow)
                .where(
                    TrackedPRRow.owner == pr_url.owner,
                    TrackedPRRow.repo == pr_url.repo,
                    TrackedPRRow.pr_number == pr_url.number,
                    TrackedPRRow.integration_id == message_ref.integration_id,
                    TrackedPRRow.message_ref == message_ref.ref,
                )
                .values(applied_emojis=_serialize_emojis(updated))
            )
            await session.execute(update_stmt)
            await session.commit()


def _serialize_emojis(emojis: frozenset[str]) -> str:
    return ",".join(sorted(emojis))


def _deserialize_emojis(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(value.split(","))


def _row_to_entity(row: TrackedPRRow) -> TrackedPR:
    return TrackedPR(
        pr_url=PRUrl(owner=row.owner, repo=row.repo, number=row.pr_number),
        message_ref=MessageRef(integration_id=row.integration_id, ref=row.message_ref),
        applied_emojis=_deserialize_emojis(row.applied_emojis),
    )
