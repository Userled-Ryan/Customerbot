from __future__ import annotations

from collections.abc import Sequence

import aiosqlite

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import EmojiReaction, PRUrl

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS tracked_prs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    channel_id TEXT NOT NULL,
    message_ts TEXT NOT NULL,
    current_emoji TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner, repo, pr_number, channel_id, message_ts)
);

CREATE INDEX IF NOT EXISTS idx_tracked_prs_lookup
ON tracked_prs(owner, repo, pr_number);
"""


class SQLitePRRepository:
    """Concrete adapter: stores tracked PRs in SQLite via aiosqlite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)
        await self._db.commit()

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
            INSERT INTO tracked_prs (owner, repo, pr_number, channel_id, message_ts, current_emoji)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner, repo, pr_number, channel_id, message_ts)
            DO UPDATE SET current_emoji = excluded.current_emoji, updated_at = CURRENT_TIMESTAMP
            """,
            (
                tracked_pr.pr_url.owner,
                tracked_pr.pr_url.repo,
                tracked_pr.pr_url.number,
                tracked_pr.channel_id,
                tracked_pr.message_ts,
                tracked_pr.current_emoji.value if tracked_pr.current_emoji else None,
            ),
        )
        await db.commit()

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]:
        db = self._get_db()
        cursor = await db.execute(
            """
            SELECT owner, repo, pr_number, channel_id, message_ts, current_emoji
            FROM tracked_prs
            WHERE owner = ? AND repo = ? AND pr_number = ?
            """,
            (pr_url.owner, pr_url.repo, pr_url.number),
        )
        rows = await cursor.fetchall()
        return [self._row_to_entity(row) for row in rows]

    async def update_emoji(
        self,
        pr_url: PRUrl,
        channel_id: str,
        message_ts: str,
        emoji: EmojiReaction,
    ) -> None:
        db = self._get_db()
        await db.execute(
            """
            UPDATE tracked_prs SET current_emoji = ?, updated_at = CURRENT_TIMESTAMP
            WHERE owner = ? AND repo = ? AND pr_number = ? AND channel_id = ? AND message_ts = ?
            """,
            (
                emoji.value,
                pr_url.owner,
                pr_url.repo,
                pr_url.number,
                channel_id,
                message_ts,
            ),
        )
        await db.commit()

    @staticmethod
    def _row_to_entity(row: aiosqlite.Row) -> TrackedPR:
        emoji = EmojiReaction(row["current_emoji"]) if row["current_emoji"] else None
        return TrackedPR(
            pr_url=PRUrl(owner=row["owner"], repo=row["repo"], number=row["pr_number"]),
            channel_id=row["channel_id"],
            message_ts=row["message_ts"],
            current_emoji=emoji,
        )
