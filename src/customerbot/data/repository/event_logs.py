from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import (
    EventCommsLogRow,
    EventPrioChangeRow,
    EventReclassificationRow,
    EventStatusChangeRow,
)
from customerbot.domain.tickets.value_objects import (
    CommsDirection,
    Priority,
    TicketStatus,
    TicketSubtype,
    TicketType,
)

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _str_to_dt(s: str) -> datetime:
    return datetime.strptime(s, _DT_FMT)


class SQLiteEventLogRepository:
    """Append-only writes for the four event-log tables.

    No update / delete methods are exposed by design. Callers should never
    need to mutate history — corrections are written as compensating rows.
    The DB enforces the same invariant via triggers in migration 0007, so
    raw SQL that bypasses this repo will still be aborted by SQLite.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append_status_change(
        self,
        ticket_id: int,
        from_status: TicketStatus | None,
        to_status: TicketStatus,
        by_user_id: str | None,
        at: datetime,
        note: str = "",
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                EventStatusChangeRow(
                    ticket_id=ticket_id,
                    from_status=from_status.value if from_status else None,
                    to_status=to_status.value,
                    by_user_id=by_user_id,
                    at=_dt_to_str(at),
                    note=note,
                )
            )
            await session.commit()

    async def append_prio_change(
        self,
        ticket_id: int,
        from_priority: Priority | None,
        to_priority: Priority,
        by_user_id: str | None,
        at: datetime,
        reason: str = "",
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                EventPrioChangeRow(
                    ticket_id=ticket_id,
                    from_priority=from_priority.value if from_priority else None,
                    to_priority=to_priority.value,
                    by_user_id=by_user_id,
                    at=_dt_to_str(at),
                    reason=reason,
                )
            )
            await session.commit()

    async def append_reclassification(
        self,
        ticket_id: int,
        from_type: TicketType,
        to_type: TicketType,
        from_subtype: TicketSubtype,
        to_subtype: TicketSubtype,
        by_user_id: str | None,
        at: datetime,
        reason: str,
        next_step: str,
        owner_user_id: str,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                EventReclassificationRow(
                    ticket_id=ticket_id,
                    from_type=from_type.value,
                    to_type=to_type.value,
                    from_subtype=from_subtype.value,
                    to_subtype=to_subtype.value,
                    by_user_id=by_user_id,
                    at=_dt_to_str(at),
                    reason=reason,
                    next_step=next_step,
                    owner_user_id=owner_user_id,
                )
            )
            await session.commit()

    async def append_comms(
        self,
        ticket_id: int,
        direction: CommsDirection,
        channel: str,
        sender_user_id: str | None,
        message_link: str | None,
        at: datetime,
        note: str = "",
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                EventCommsLogRow(
                    ticket_id=ticket_id,
                    direction=direction.value,
                    channel=channel,
                    sender_user_id=sender_user_id,
                    message_link=message_link,
                    at=_dt_to_str(at),
                    note=note,
                )
            )
            await session.commit()

    async def last_status_change_into(
        self,
        ticket_id: int,
        to_status: TicketStatus,
    ) -> datetime | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(EventStatusChangeRow.at)
                .where(
                    EventStatusChangeRow.ticket_id == ticket_id,
                    EventStatusChangeRow.to_status == to_status.value,
                )
                .order_by(EventStatusChangeRow.at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return _str_to_dt(row) if row else None
