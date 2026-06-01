from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import TicketLinkRow, TicketOrgRow, TicketRow
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    LIVE_STATUSES,
    Lane,
    Priority,
    Severity,
    Source,
    TicketLinkRelation,
    TicketStatus,
    TicketSubtype,
    TicketType,
)

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


def _row_to_ticket(row: TicketRow) -> Ticket:
    return Ticket(
        id=row.id,
        title=row.title,
        type=TicketType(row.type),
        subtype=TicketSubtype(row.subtype),
        status=TicketStatus(row.status),
        lane=Lane(row.lane) if row.lane else None,
        priority=Priority(row.priority),
        severity=Severity(row.severity),
        feature=row.feature,
        description=row.description,
        reporter_user_id=row.reporter_user_id,
        assigned_user_id=row.assigned_user_id,
        source=Source(row.source),
        original_slack_link=row.original_slack_link,
        prod_link=row.prod_link,
        screenshot_url=row.screenshot_url,
        replay_link=row.replay_link,
        affected_user=row.affected_user,
        blocking_impact=row.blocking_impact,
        deadline=_str_to_date(row.deadline) if row.deadline else None,
        card_channel_id=row.card_channel_id,
        card_message_ts=row.card_message_ts,
        created_at=_str_to_dt(row.created_at),
        first_response_at=_str_to_dt(row.first_response_at) if row.first_response_at else None,
        resolved_at=_str_to_dt(row.resolved_at) if row.resolved_at else None,
        closed_at=_str_to_dt(row.closed_at) if row.closed_at else None,
        updated_at=_str_to_dt(row.updated_at),
    )


class SQLiteTicketRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, ticket: Ticket) -> Ticket:
        async with self._session_factory() as session:
            row = TicketRow(
                title=ticket.title,
                type=ticket.type.value,
                subtype=ticket.subtype.value,
                status=ticket.status.value,
                lane=ticket.lane.value if ticket.lane else None,
                priority=ticket.priority.value,
                severity=ticket.severity.value,
                feature=ticket.feature,
                description=ticket.description,
                reporter_user_id=ticket.reporter_user_id,
                assigned_user_id=ticket.assigned_user_id,
                source=ticket.source.value,
                original_slack_link=ticket.original_slack_link,
                prod_link=ticket.prod_link,
                screenshot_url=ticket.screenshot_url,
                replay_link=ticket.replay_link,
                affected_user=ticket.affected_user,
                blocking_impact=ticket.blocking_impact,
                deadline=_date_to_str(ticket.deadline) if ticket.deadline else None,
                card_channel_id=ticket.card_channel_id,
                card_message_ts=ticket.card_message_ts,
                created_at=_dt_to_str(ticket.created_at),
                first_response_at=_dt_to_str(ticket.first_response_at)
                if ticket.first_response_at
                else None,
                resolved_at=_dt_to_str(ticket.resolved_at) if ticket.resolved_at else None,
                closed_at=_dt_to_str(ticket.closed_at) if ticket.closed_at else None,
                updated_at=_dt_to_str(ticket.updated_at),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_ticket(row)

    async def get(self, ticket_id: int) -> Ticket | None:
        async with self._session_factory() as session:
            row = await session.get(TicketRow, ticket_id)
            return _row_to_ticket(row) if row else None

    async def update_status(
        self,
        ticket_id: int,
        status: TicketStatus,
        *,
        now: datetime,
    ) -> None:
        now_str = _dt_to_str(now)
        values: dict[str, str] = {"status": status.value, "updated_at": now_str}
        # Ambiguity #8: first_response_at fires on New → In progress.
        if status == TicketStatus.IN_PROGRESS:
            # Only set if not already set; SQLite-friendly via CASE-style update below.
            async with self._session_factory() as session:
                row = await session.get(TicketRow, ticket_id)
                if row is None:
                    return
                row.status = status.value
                row.updated_at = now_str
                if row.first_response_at is None:
                    row.first_response_at = now_str
                if status == TicketStatus.RESOLVED:
                    row.resolved_at = now_str
                await session.commit()
            return
        if status == TicketStatus.RESOLVED:
            values["resolved_at"] = now_str
        if status == TicketStatus.CLOSED:
            values["closed_at"] = now_str
        async with self._session_factory() as session:
            await session.execute(
                update(TicketRow).where(TicketRow.id == ticket_id).values(**values)
            )
            await session.commit()

    async def update_priority(self, ticket_id: int, priority: Priority, *, now: datetime) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(TicketRow)
                .where(TicketRow.id == ticket_id)
                .values(priority=priority.value, updated_at=_dt_to_str(now))
            )
            await session.commit()

    async def update_lane(self, ticket_id: int, lane: Lane, *, now: datetime) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(TicketRow)
                .where(TicketRow.id == ticket_id)
                .values(lane=lane.value, updated_at=_dt_to_str(now))
            )
            await session.commit()

    async def update_card_message(self, ticket_id: int, channel_id: str, message_ts: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(TicketRow)
                .where(TicketRow.id == ticket_id)
                .values(card_channel_id=channel_id, card_message_ts=message_ts)
            )
            await session.commit()

    async def update_feature(self, ticket_id: int, feature: str | None) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(TicketRow).where(TicketRow.id == ticket_id).values(feature=feature)
            )
            await session.commit()

    async def update_type_subtype(
        self,
        ticket_id: int,
        ticket_type: TicketType,
        subtype: TicketSubtype,
        *,
        now: datetime,
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(TicketRow)
                .where(TicketRow.id == ticket_id)
                .values(
                    type=ticket_type.value,
                    subtype=subtype.value,
                    updated_at=_dt_to_str(now),
                )
            )
            await session.commit()

    async def query_live(self) -> list[Ticket]:
        live = [s.value for s in LIVE_STATUSES]
        async with self._session_factory() as session:
            result = await session.execute(
                select(TicketRow).where(TicketRow.status.in_(live)).order_by(TicketRow.id)
            )
            return [_row_to_ticket(r) for r in result.scalars().all()]

    async def find_by_slack_link(self, slack_link: str) -> Ticket | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TicketRow).where(TicketRow.original_slack_link == slack_link)
            )
            row = result.scalar_one_or_none()
            return _row_to_ticket(row) if row else None

    async def add_org(self, ticket_id: int, org_id: str) -> None:
        async with self._session_factory() as session:
            stmt = (
                insert(TicketOrgRow)
                .values(ticket_id=ticket_id, org_id=org_id)
                .on_conflict_do_nothing(index_elements=["ticket_id", "org_id"])
            )
            await session.execute(stmt)
            await session.commit()

    async def list_orgs(self, ticket_id: int) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TicketOrgRow.org_id)
                .where(TicketOrgRow.ticket_id == ticket_id)
                .order_by(TicketOrgRow.added_at)
            )
            return [r for r in result.scalars().all()]

    async def add_link(
        self, from_ticket_id: int, to_ticket_id: int, relation: TicketLinkRelation
    ) -> None:
        async with self._session_factory() as session:
            stmt = (
                insert(TicketLinkRow)
                .values(
                    from_ticket_id=from_ticket_id,
                    to_ticket_id=to_ticket_id,
                    relation=relation.value,
                )
                .on_conflict_do_nothing(
                    index_elements=["from_ticket_id", "to_ticket_id", "relation"]
                )
            )
            await session.execute(stmt)
            await session.commit()
