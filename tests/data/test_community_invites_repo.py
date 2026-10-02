from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.community.sweep import (
    ALREADY_MEMBER,
    FAILED,
    INVITED,
    LEFT,
    CommunityInvite,
)
from customerbot.data.repository.community_invites import SQLiteCommunityInviteRepository


async def test_record_round_trips(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteCommunityInviteRepository(session_factory)
    await repo.record(
        CommunityInvite(
            user_id="U1",
            status=INVITED,
            team_id="T_ACME",
            email="a@acme.com",
            source_org="Acme",
            source_channel_id="C_ACME",
        )
    )
    [row] = await repo.list_all()
    assert (row.user_id, row.status, row.team_id, row.email) == (
        "U1",
        INVITED,
        "T_ACME",
        "a@acme.com",
    )
    assert (row.source_org, row.source_channel_id, row.detail) == ("Acme", "C_ACME", None)
    assert row.created_at is not None


async def test_every_status_but_failed_blocks_reinvite(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteCommunityInviteRepository(session_factory)
    for uid, status in [("U_I", INVITED), ("U_M", ALREADY_MEMBER), ("U_L", LEFT), ("U_F", FAILED)]:
        await repo.record(CommunityInvite(user_id=uid, status=status))
    assert await repo.blocked_user_ids() == {"U_I", "U_M", "U_L"}


async def test_left_overwrites_invited_so_leaver_stays_blocked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteCommunityInviteRepository(session_factory)
    await repo.record(CommunityInvite(user_id="U1", status=INVITED, source_org="Acme"))
    await repo.record(CommunityInvite(user_id="U1", status=LEFT))
    [row] = await repo.list_all()
    assert row.status == LEFT
    assert "U1" in await repo.blocked_user_ids()


async def test_failed_retry_can_become_invited(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteCommunityInviteRepository(session_factory)
    await repo.record(CommunityInvite(user_id="U1", status=FAILED, detail="cant_invite"))
    await repo.record(CommunityInvite(user_id="U1", status=INVITED))
    [row] = await repo.list_all()
    assert (row.status, row.detail) == (INVITED, None)


async def test_no_overwrite_keeps_existing_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteCommunityInviteRepository(session_factory)
    await repo.record(CommunityInvite(user_id="U1", status=INVITED, source_org="Acme"))
    await repo.record(CommunityInvite(user_id="U1", status=ALREADY_MEMBER), overwrite=False)
    await repo.record(CommunityInvite(user_id="U2", status=ALREADY_MEMBER), overwrite=False)
    rows = {r.user_id: r for r in await repo.list_all()}
    assert (rows["U1"].status, rows["U1"].source_org) == (INVITED, "Acme")
    assert rows["U2"].status == ALREADY_MEMBER
