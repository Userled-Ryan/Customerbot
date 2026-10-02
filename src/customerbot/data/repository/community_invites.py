from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.community.sweep import BLOCKING_STATUSES, CommunityInvite
from customerbot.data.database import CommunityInviteRow

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _row_to_invite(row: CommunityInviteRow) -> CommunityInvite:
    return CommunityInvite(
        user_id=row.user_id,
        status=row.status,
        team_id=row.team_id,
        email=row.email,
        source_org=row.source_org,
        source_channel_id=row.source_channel_id,
        detail=row.detail,
        created_at=datetime.strptime(row.created_at, _DT_FMT),
        updated_at=datetime.strptime(row.updated_at, _DT_FMT),
    )


class SQLiteCommunityInviteRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_all(self) -> list[CommunityInvite]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CommunityInviteRow).order_by(CommunityInviteRow.created_at)
            )
            return [_row_to_invite(r) for r in result.scalars().all()]

    async def blocked_user_ids(self) -> set[str]:
        """Users the sweep must never invite: every status except `failed`."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(CommunityInviteRow.user_id).where(
                    CommunityInviteRow.status.in_(BLOCKING_STATUSES)
                )
            )
            return set(result.scalars().all())

    async def record(self, invite: CommunityInvite, *, overwrite: bool = True) -> None:
        """Upsert one ledger row. With `overwrite=False` an existing row wins —
        used when syncing current members, so an `invited` or `left` row isn't
        relabelled `already_member`."""
        now = _utcnow().strftime(_DT_FMT)
        values = {
            "status": invite.status,
            "team_id": invite.team_id,
            "email": invite.email,
            "source_org": invite.source_org,
            "source_channel_id": invite.source_channel_id,
            "detail": invite.detail,
            "updated_at": now,
        }
        stmt = insert(CommunityInviteRow).values(user_id=invite.user_id, created_at=now, **values)
        if overwrite:
            stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_=values)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["user_id"])
        async with self._session_factory() as session:
            await session.execute(stmt)
            await session.commit()
