from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prbot.data.database import ChannelCursorRow, TrackedPRRow
from prbot.domain.tracking.entities import TrackedPR
from prbot.domain.tracking.value_objects import MessageRef, PRUrl


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
                    scope_keys=_serialize_scope_keys(tracked_pr.scope_keys),
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
                        "scope_keys": _serialize_scope_keys(tracked_pr.scope_keys),
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

    async def find_distinct_pr_urls(self) -> Sequence[PRUrl]:
        async with self._session_factory() as session:
            stmt = select(
                TrackedPRRow.owner,
                TrackedPRRow.repo,
                TrackedPRRow.pr_number,
            ).distinct()
            result = await session.execute(stmt)
            return [
                PRUrl(owner=row.owner, repo=row.repo, number=row.pr_number) for row in result.all()
            ]

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


class SQLiteChannelCursorRepository:
    """Concrete adapter: stores channel cursors in SQLite via SQLAlchemy async."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_cursor(self, integration_id: str, channel_id: str) -> str | None:
        async with self._session_factory() as session:
            stmt = select(ChannelCursorRow.last_seen_ts).where(
                ChannelCursorRow.integration_id == integration_id,
                ChannelCursorRow.channel_id == channel_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def seed_missing_from_tracked_prs(self, integration_id: str) -> int:
        """Pre-fill cursors from existing tracked PR timestamps.

        For channels that have tracked PRs but no cursor yet, derive the
        cursor from the latest message_ref timestamp. Returns the number
        of cursors seeded.
        """
        async with self._session_factory() as session:
            # Find all message_refs for this integration
            stmt = select(
                TrackedPRRow.message_ref,
            ).where(TrackedPRRow.integration_id == integration_id)
            result = await session.execute(stmt)
            refs = result.scalars().all()

        # Parse channel:ts from message_refs and find max ts per channel
        latest_per_channel: dict[str, str] = {}
        for ref in refs:
            if ":" not in ref:
                continue
            channel, ts = ref.rsplit(":", 1)
            if channel not in latest_per_channel or ts > latest_per_channel[channel]:
                latest_per_channel[channel] = ts

        seeded = 0
        for channel, ts in latest_per_channel.items():
            existing = await self.get_cursor(integration_id, channel)
            if existing is None:
                await self.upsert_cursor(integration_id, channel, ts)
                seeded += 1

        return seeded

    async def upsert_cursor(self, integration_id: str, channel_id: str, ts: str) -> None:
        """Write the cursor for a channel. Callers are responsible for monotonicity.

        A DB-level `MAX(existing, new)` guard was tempting but does a lexicographic
        string comparison, which breaks across cursor-format changes (e.g. Slack
        float "1776550080.x" lex-compares greater than a Discord snowflake
        "1495187...", so the stale float would win and reject new advances).
        """
        async with self._session_factory() as session:
            stmt = (
                insert(ChannelCursorRow)
                .values(
                    integration_id=integration_id,
                    channel_id=channel_id,
                    last_seen_ts=ts,
                )
                .on_conflict_do_update(
                    index_elements=["integration_id", "channel_id"],
                    set_={
                        "last_seen_ts": ts,
                        "updated_at": func.current_timestamp(),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()


def _serialize_emojis(emojis: frozenset[str]) -> str:
    return ",".join(sorted(emojis))


def _deserialize_emojis(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(value.split(","))


def _serialize_scope_keys(keys: tuple[str, ...]) -> str:
    return ",".join(keys)


def _deserialize_scope_keys(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(value.split(","))


def _row_to_entity(row: TrackedPRRow) -> TrackedPR:
    return TrackedPR(
        pr_url=PRUrl(owner=row.owner, repo=row.repo, number=row.pr_number),
        message_ref=MessageRef(integration_id=row.integration_id, ref=row.message_ref),
        applied_emojis=_deserialize_emojis(row.applied_emojis),
        scope_keys=_deserialize_scope_keys(row.scope_keys),
    )
