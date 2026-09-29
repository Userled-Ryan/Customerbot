from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import SeAvailabilityRow
from customerbot.domain.tickets.entities import SeAbsence

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _row_to_absence(row: SeAvailabilityRow) -> SeAbsence:
    return SeAbsence(
        user_id=row.user_id,
        cover_user_id=row.cover_user_id,
        back_on=date.fromisoformat(row.back_on) if row.back_on else None,
        set_by_user_id=row.set_by_user_id,
        created_at=datetime.strptime(row.created_at, _DT_FMT),
        updated_at=datetime.strptime(row.updated_at, _DT_FMT),
    )


class SQLiteSeAvailabilityRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_all(self) -> list[SeAbsence]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SeAvailabilityRow).order_by(SeAvailabilityRow.created_at)
            )
            return [_row_to_absence(r) for r in result.scalars().all()]

    async def upsert(self, absence: SeAbsence) -> None:
        now = _utcnow().strftime(_DT_FMT)
        values = {
            "cover_user_id": absence.cover_user_id,
            "back_on": absence.back_on.isoformat() if absence.back_on else None,
            "set_by_user_id": absence.set_by_user_id,
            "updated_at": now,
        }
        async with self._session_factory() as session:
            await session.execute(
                insert(SeAvailabilityRow)
                .values(user_id=absence.user_id, created_at=now, **values)
                .on_conflict_do_update(index_elements=["user_id"], set_=values)
            )
            await session.commit()

    async def delete(self, user_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(SeAvailabilityRow)
                .where(SeAvailabilityRow.user_id == user_id)
                .returning(SeAvailabilityRow.user_id)
            )
            deleted = result.scalar_one_or_none() is not None
            await session.commit()
            return deleted
