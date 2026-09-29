from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import DeployHoldRow, ReleasePRRow, ReleaseRow
from customerbot.domain.tickets.releases import DeployHold, Release

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _opt_str_to_dt(s: str | None) -> datetime | None:
    return datetime.strptime(s, _DT_FMT) if s else None


def _row_to_hold(row: DeployHoldRow) -> DeployHold:
    return DeployHold(
        ticket_id=row.ticket_id,
        pr_number=row.pr_number,
        pr_url=row.pr_url,
        created_at=datetime.strptime(row.created_at, _DT_FMT),
        deployed_at=_opt_str_to_dt(row.deployed_at),
        nudged_at=_opt_str_to_dt(row.nudged_at),
    )


_OPEN = (DeployHoldRow.released_at.is_(None), DeployHoldRow.cancelled_at.is_(None))


class SQLiteReleaseRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(self, release_ts: str, channel_id: str, *, now: datetime) -> None:
        async with self._session_factory() as session:
            await session.execute(
                insert(ReleaseRow)
                .values(release_ts=release_ts, channel_id=channel_id, created_at=_dt_to_str(now))
                .on_conflict_do_nothing(index_elements=["release_ts"])
            )
            await session.commit()

    async def get(self, release_ts: str) -> Release | None:
        async with self._session_factory() as session:
            row = await session.get(ReleaseRow, release_ts)
            if row is None:
                return None
            return Release(
                release_ts=row.release_ts,
                channel_id=row.channel_id,
                run_url=row.run_url,
                deployed_at=_opt_str_to_dt(row.deployed_at),
            )

    async def add_prs(self, release_ts: str, pr_numbers: Collection[int]) -> None:
        if not pr_numbers:
            return
        async with self._session_factory() as session:
            await session.execute(
                insert(ReleasePRRow)
                .values([{"release_ts": release_ts, "pr_number": n} for n in pr_numbers])
                .on_conflict_do_nothing(index_elements=["release_ts", "pr_number"])
            )
            await session.commit()

    async def list_prs(self, release_ts: str) -> set[int]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ReleasePRRow.pr_number).where(ReleasePRRow.release_ts == release_ts)
            )
            return set(result.scalars().all())

    async def mark_deployed(self, release_ts: str, run_url: str, *, now: datetime) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(ReleaseRow)
                .where(ReleaseRow.release_ts == release_ts)
                .values(run_url=run_url, deployed_at=_dt_to_str(now))
            )
            await session.commit()

    async def deployed_prs(self, pr_numbers: Collection[int]) -> set[int]:
        if not pr_numbers:
            return set()
        async with self._session_factory() as session:
            result = await session.execute(
                select(ReleasePRRow.pr_number)
                .join(ReleaseRow, ReleaseRow.release_ts == ReleasePRRow.release_ts)
                .where(
                    ReleasePRRow.pr_number.in_(list(pr_numbers)),
                    ReleaseRow.deployed_at.is_not(None),
                )
            )
            return set(result.scalars().all())


class SQLiteDeployHoldRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add(self, ticket_id: int, pr_number: int, pr_url: str, *, now: datetime) -> None:
        now_str = _dt_to_str(now)
        fresh = {
            "pr_url": pr_url,
            "created_at": now_str,
            "deployed_at": None,
            "nudged_at": None,
            "released_at": None,
            "cancelled_at": None,
        }
        async with self._session_factory() as session:
            # UNIQUE(ticket_id, pr_number): a ticket reopened and re-resolved on
            # the same PR restarts its hold rather than inheriting the old one.
            await session.execute(
                insert(DeployHoldRow)
                .values(ticket_id=ticket_id, pr_number=pr_number, **fresh)
                .on_conflict_do_update(index_elements=["ticket_id", "pr_number"], set_=fresh)
            )
            await session.commit()

    async def list_open_for_ticket(self, ticket_id: int) -> list[DeployHold]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DeployHoldRow)
                .where(DeployHoldRow.ticket_id == ticket_id, *_OPEN)
                .order_by(DeployHoldRow.pr_number)
            )
            return [_row_to_hold(r) for r in result.scalars().all()]

    async def mark_deployed(self, pr_numbers: Collection[int], *, now: datetime) -> list[int]:
        if not pr_numbers:
            return []
        where = (DeployHoldRow.pr_number.in_(list(pr_numbers)), *_OPEN)
        async with self._session_factory() as session:
            result = await session.execute(select(DeployHoldRow.ticket_id).where(*where).distinct())
            ticket_ids = sorted(result.scalars().all())
            await session.execute(
                update(DeployHoldRow)
                .where(*where, DeployHoldRow.deployed_at.is_(None))
                .values(deployed_at=_dt_to_str(now))
            )
            await session.commit()
            return ticket_ids

    async def close_for_ticket(self, ticket_id: int, *, released: bool, now: datetime) -> None:
        column = "released_at" if released else "cancelled_at"
        async with self._session_factory() as session:
            await session.execute(
                update(DeployHoldRow)
                .where(DeployHoldRow.ticket_id == ticket_id, *_OPEN)
                .values({column: _dt_to_str(now)})
            )
            await session.commit()

    async def list_due_for_nudge(self, *, created_before: datetime) -> list[DeployHold]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DeployHoldRow)
                .where(
                    *_OPEN,
                    DeployHoldRow.deployed_at.is_(None),
                    DeployHoldRow.nudged_at.is_(None),
                    DeployHoldRow.created_at <= _dt_to_str(created_before),
                )
                .order_by(DeployHoldRow.ticket_id, DeployHoldRow.pr_number)
            )
            return [_row_to_hold(r) for r in result.scalars().all()]

    async def mark_nudged(self, ticket_id: int, *, now: datetime) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(DeployHoldRow)
                .where(DeployHoldRow.ticket_id == ticket_id, *_OPEN)
                .values(nudged_at=_dt_to_str(now))
            )
            await session.commit()
