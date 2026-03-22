from __future__ import annotations

from collections.abc import Sequence

import aiosqlite

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import PRUrl

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS tracked_prs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    channel_id TEXT NOT NULL,
    message_ts TEXT NOT NULL,
    applied_emojis TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner, repo, pr_number, channel_id, message_ts)
);

CREATE INDEX IF NOT EXISTS idx_tracked_prs_lookup
ON tracked_prs(owner, repo, pr_number);
"""

_MIGRATE_SQL = """
ALTER TABLE tracked_prs RENAME COLUMN current_emoji TO applied_emojis;
"""


class SQLitePRRepository:
    """Concrete adapter: stores tracked PRs in SQLite via aiosqlite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._migrate()
        await self._db.executescript(_INIT_SQL)
        await self._db.commit()

    async def _migrate(self) -> None:
        """Run schema migrations for existing databases."""
        db = self._get_db()
        cursor = await db.execute("PRAGMA table_info(tracked_prs)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "current_emoji" in columns:
            await db.executescript(_MIGRATE_SQL)
            await db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "Database not initialized. Call initialize() first."
            raise RuntimeError(msg)
        return self._db

    async def save(self, tracked_pr: TrackedPR) -> None:
        db = self._get_db()
        await db.execute(
            """
            INSERT INTO tracked_prs (owner, repo, pr_number, channel_id, message_ts, applied_emojis)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner, repo, pr_number, channel_id, message_ts)
            DO UPDATE SET applied_emojis = excluded.applied_emojis, updated_at = CURRENT_TIMESTAMP
            """,
            (
                tracked_pr.pr_url.owner,
                tracked_pr.pr_url.repo,
                tracked_pr.pr_url.number,
                tracked_pr.channel_id,
                tracked_pr.message_ts,
                _serialize_emojis(tracked_pr.applied_emojis),
            ),
        )
        await db.commit()

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]:
        db = self._get_db()
        cursor = await db.execute(
            """
            SELECT owner, repo, pr_number, channel_id, message_ts, applied_emojis
            FROM tracked_prs
            WHERE owner = ? AND repo = ? AND pr_number = ?
            """,
            (pr_url.owner, pr_url.repo, pr_url.number),
        )
        rows = await cursor.fetchall()
        return [_row_to_entity(row) for row in rows]

    async def add_emoji(
        self,
        pr_url: PRUrl,
        channel_id: str,
        message_ts: str,
        emoji: str,
    ) -> None:
        db = self._get_db()
        # Fetch current emojis, append the new one, and write back.
        cursor = await db.execute(
            """
            SELECT applied_emojis FROM tracked_prs
            WHERE owner = ? AND repo = ? AND pr_number = ? AND channel_id = ? AND message_ts = ?
            """,
            (pr_url.owner, pr_url.repo, pr_url.number, channel_id, message_ts),
        )
        row = await cursor.fetchone()
        if row is None:
            return

        current = _deserialize_emojis(row["applied_emojis"])
        updated = current | {emoji}

        await db.execute(
            """
            UPDATE tracked_prs SET applied_emojis = ?, updated_at = CURRENT_TIMESTAMP
            WHERE owner = ? AND repo = ? AND pr_number = ? AND channel_id = ? AND message_ts = ?
            """,
            (
                _serialize_emojis(updated),
                pr_url.owner,
                pr_url.repo,
                pr_url.number,
                channel_id,
                message_ts,
            ),
        )
        await db.commit()


def _serialize_emojis(emojis: frozenset[str]) -> str:
    return ",".join(sorted(emojis))


def _deserialize_emojis(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(value.split(","))


def _row_to_entity(row: aiosqlite.Row) -> TrackedPR:
    return TrackedPR(
        pr_url=PRUrl(owner=row["owner"], repo=row["repo"], number=row["pr_number"]),
        channel_id=row["channel_id"],
        message_ts=row["message_ts"],
        applied_emojis=_deserialize_emojis(row["applied_emojis"]),
    )
