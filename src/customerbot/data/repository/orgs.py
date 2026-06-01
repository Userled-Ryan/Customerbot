from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import OrgRow
from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import ACVTier, RenewalStatus, Sentiment

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"
_DATE_FMT = "%Y-%m-%d"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _str_to_dt(s: str) -> datetime:
    return datetime.strptime(s, _DT_FMT)


def _date_to_str(d: date) -> str:
    return d.strftime(_DATE_FMT)


def _str_to_date(s: str) -> date:
    return datetime.strptime(s, _DATE_FMT).date()


def _row_to_org(row: OrgRow) -> Org:
    return Org(
        id=row.id,
        name=row.name,
        slack_channel_id=row.slack_channel_id,
        acv_tier=ACVTier(row.acv_tier) if row.acv_tier else None,
        sentiment=Sentiment(row.sentiment) if row.sentiment else None,
        renewal_date=_str_to_date(row.renewal_date) if row.renewal_date else None,
        renewal_status=RenewalStatus(row.renewal_status) if row.renewal_status else None,
        csm_user_id=row.csm_user_id,
        created_at=_str_to_dt(row.created_at),
        updated_at=_str_to_dt(row.updated_at),
    )


class SQLiteOrgRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(self, org: Org) -> None:
        values = {
            "id": org.id,
            "name": org.name,
            "slack_channel_id": org.slack_channel_id,
            "acv_tier": org.acv_tier.value if org.acv_tier else None,
            "sentiment": org.sentiment.value if org.sentiment else None,
            "renewal_date": _date_to_str(org.renewal_date) if org.renewal_date else None,
            "renewal_status": org.renewal_status.value if org.renewal_status else None,
            "csm_user_id": org.csm_user_id,
            "created_at": _dt_to_str(org.created_at),
            "updated_at": _dt_to_str(org.updated_at),
        }
        update_values = {k: v for k, v in values.items() if k != "id" and k != "created_at"}
        async with self._session_factory() as session:
            stmt = (
                insert(OrgRow)
                .values(**values)
                .on_conflict_do_update(index_elements=["id"], set_=update_values)
            )
            await session.execute(stmt)
            await session.commit()

    async def get(self, org_id: str) -> Org | None:
        async with self._session_factory() as session:
            row = await session.get(OrgRow, org_id)
            return _row_to_org(row) if row else None

    async def find_by_slack_channel(self, slack_channel_id: str) -> Org | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(OrgRow).where(OrgRow.slack_channel_id == slack_channel_id)
            )
            row = result.scalar_one_or_none()
            return _row_to_org(row) if row else None

    async def list_all(self) -> list[Org]:
        async with self._session_factory() as session:
            result = await session.execute(select(OrgRow).order_by(OrgRow.name))
            return [_row_to_org(r) for r in result.scalars().all()]
