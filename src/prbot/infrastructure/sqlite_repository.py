from __future__ import annotations

from collections.abc import Sequence

import aiosqlite

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import MessageRef, PRUrl

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS tracked_prs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    integration_id TEXT NOT NULL DEFAULT 'slack',
    message_ref TEXT NOT NULL,
    applied_emojis TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner, repo, pr_number, integration_id, message_ref)
);

CREATE INDEX IF NOT EXISTS idx_tracked_prs_lookup
ON tracked_prs(owner, repo, pr_number);
"""

_MIGRATE_RENAME_EMOJI_SQL = """
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

        if not columns:
            # Table doesn't exist yet, will be created by _INIT_SQL.
            return

        # Migration 1: rename current_emoji -> applied_emojis
        if "current_emoji" in columns:
            await db.executescript(_MIGRATE_RENAME_EMOJI_SQL)
            await db.commit()
            # Refresh columns after migration
            cursor = await db.execute("PRAGMA table_info(tracked_prs)")
            columns = [row[1] for row in await cursor.fetchall()]

        # Migration 2: channel_id/message_ts -> integration_id/message_ref
        if "channel_id" in columns and "integration_id" not in columns:
            await db.execute(
                "ALTER TABLE tracked_prs ADD COLUMN integration_id TEXT NOT NULL DEFAULT 'slack'"
            )
            await db.execute(
                "ALTER TABLE tracked_prs ADD COLUMN message_ref TEXT NOT NULL DEFAULT ''"
            )
            await db.execute(
                "UPDATE tracked_prs"
                " SET message_ref = channel_id || ':' || message_ts"
                " WHERE message_ref = ''"
            )
            # SQLite doesn't support DROP COLUMN before 3.35.0, so we recreate the table.
            await db.executescript("""
CREATE TABLE tracked_prs_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    integration_id TEXT NOT NULL DEFAULT 'slack',
    message_ref TEXT NOT NULL,
    applied_emojis TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner, repo, pr_number, integration_id, message_ref)
);

INSERT INTO tracked_prs_new (
    id, owner, repo, pr_number, integration_id,
    message_ref, applied_emojis, created_at, updated_at
)
SELECT
    id, owner, repo, pr_number, integration_id,
    message_ref, applied_emojis, created_at, updated_at
FROM tracked_prs;

DROP TABLE tracked_prs;
ALTER TABLE tracked_prs_new RENAME TO tracked_prs;

CREATE INDEX IF NOT EXISTS idx_tracked_prs_lookup
ON tracked_prs(owner, repo, pr_number);
            """)
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
            INSERT INTO tracked_prs (
                owner, repo, pr_number,
                integration_id, message_ref, applied_emojis
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner, repo, pr_number, integration_id, message_ref)
            DO UPDATE SET applied_emojis = excluded.applied_emojis, updated_at = CURRENT_TIMESTAMP
            """,
            (
                tracked_pr.pr_url.owner,
                tracked_pr.pr_url.repo,
                tracked_pr.pr_url.number,
                tracked_pr.message_ref.integration_id,
                tracked_pr.message_ref.ref,
                _serialize_emojis(tracked_pr.applied_emojis),
            ),
        )
        await db.commit()

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]:
        db = self._get_db()
        cursor = await db.execute(
            """
            SELECT owner, repo, pr_number, integration_id, message_ref, applied_emojis
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
        message_ref: MessageRef,
        emoji: str,
    ) -> None:
        db = self._get_db()
        cursor = await db.execute(
            """
            SELECT applied_emojis FROM tracked_prs
            WHERE owner = ? AND repo = ? AND pr_number = ?
              AND integration_id = ? AND message_ref = ?
            """,
            (pr_url.owner, pr_url.repo, pr_url.number, message_ref.integration_id, message_ref.ref),
        )
        row = await cursor.fetchone()
        if row is None:
            return

        current = _deserialize_emojis(row["applied_emojis"])
        updated = current | {emoji}

        await db.execute(
            """
            UPDATE tracked_prs SET applied_emojis = ?, updated_at = CURRENT_TIMESTAMP
            WHERE owner = ? AND repo = ? AND pr_number = ?
              AND integration_id = ? AND message_ref = ?
            """,
            (
                _serialize_emojis(updated),
                pr_url.owner,
                pr_url.repo,
                pr_url.number,
                message_ref.integration_id,
                message_ref.ref,
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
        message_ref=MessageRef(integration_id=row["integration_id"], ref=row["message_ref"]),
        applied_emojis=_deserialize_emojis(row["applied_emojis"]),
    )
