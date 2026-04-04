"""One-time script: seed channel cursors.

Can either derive cursors from existing tracked PRs, or force all
cursors to a specific date so the backfill replays from that point.

Usage:
    # Derive from tracked PRs:
    uv run python scripts/seed_cursors.py

    # Force all cursors to a specific date:
    uv run python scripts/seed_cursors.py --since 2026-03-28
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import update

from prbot.config import Settings
from prbot.data.database import (
    ChannelCursorRow,
    database_url_from_path,
    make_engine,
    make_session_factory,
)
from prbot.data.repository import SQLiteChannelCursorRepository
from prbot.integration.slack.gateway import INTEGRATION_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed channel cursors")
    parser.add_argument(
        "--since",
        type=str,
        help="Force all cursors to this date (YYYY-MM-DD). Backfill will replay from this point.",
    )
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    database_url = database_url_from_path(settings.database_path)
    engine = make_engine(database_url)
    session_factory = make_session_factory(engine)
    cursor_repo = SQLiteChannelCursorRepository(session_factory=session_factory)

    if args.since:
        dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=UTC)
        ts = f"{dt.timestamp():.6f}"

        # Ensure cursors exist for all channels with tracked PRs
        await cursor_repo.seed_missing_from_tracked_prs(INTEGRATION_ID)

        # Force all cursors backward to the target date
        async with session_factory() as session:
            stmt = (
                update(ChannelCursorRow)
                .where(ChannelCursorRow.integration_id == INTEGRATION_ID)
                .values(last_seen_ts=ts)
            )
            await session.execute(stmt)
            await session.commit()

        logger.info("Forced all cursors to %s (%s)", args.since, ts)
    else:
        seeded = await cursor_repo.seed_missing_from_tracked_prs(INTEGRATION_ID)
        logger.info("Seeded %d channel cursors from tracked PRs", seeded)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
