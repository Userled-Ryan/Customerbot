from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import ACVTier, RenewalStatus, Sentiment


@pytest.mark.asyncio
async def test_upsert_and_get_org(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteOrgRepository(session_factory)
    await repo.upsert(
        Org(
            id="acme",
            name="Acme Corp",
            slack_channel_id="C_ACME",
            acv_tier=ACVTier.LARGE,
            sentiment=Sentiment.NEUTRAL,
            renewal_date=date(2026, 12, 1),
            renewal_status=RenewalStatus.STABLE,
            csm_user_id="U_CSM",
        )
    )

    got = await repo.get("acme")
    assert got is not None
    assert got.name == "Acme Corp"
    assert got.slack_channel_id == "C_ACME"
    assert got.acv_tier == ACVTier.LARGE
    assert got.sentiment == Sentiment.NEUTRAL
    assert got.renewal_date == date(2026, 12, 1)
    assert got.renewal_status == RenewalStatus.STABLE
    assert got.csm_user_id == "U_CSM"


@pytest.mark.asyncio
async def test_find_by_slack_channel(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Ambiguity #1 resolution: channel→org lookup via slack_channel_id column."""
    repo = SQLiteOrgRepository(session_factory)
    await repo.upsert(Org(id="acme", name="Acme", slack_channel_id="C_ACME"))
    await repo.upsert(Org(id="globex", name="Globex", slack_channel_id="C_GLOBEX"))

    hit = await repo.find_by_slack_channel("C_ACME")
    assert hit is not None
    assert hit.id == "acme"

    miss = await repo.find_by_slack_channel("C_UNKNOWN")
    assert miss is None


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_updates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteOrgRepository(session_factory)
    await repo.upsert(Org(id="acme", name="Acme Corp", sentiment=Sentiment.NEUTRAL))
    await repo.upsert(Org(id="acme", name="Acme Corp", sentiment=Sentiment.NEGATIVE))

    got = await repo.get("acme")
    assert got is not None
    assert got.sentiment == Sentiment.NEGATIVE


@pytest.mark.asyncio
async def test_list_all_orgs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteOrgRepository(session_factory)
    await repo.upsert(Org(id="zeta", name="Zeta"))
    await repo.upsert(Org(id="acme", name="Acme"))

    listed = await repo.list_all()
    assert [o.id for o in listed] == ["acme", "zeta"]  # ordered by name
