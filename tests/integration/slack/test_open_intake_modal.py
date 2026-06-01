from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.open_intake_modal import OpenIntakeModal
from customerbot.data.repository.bot_state import SQLiteDraftFormSessionRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.domain.bot_state.entities import ModalKind
from customerbot.domain.tickets.entities import Org
from customerbot.integration.slack.modals import csm_intake, se_bug
from tests.conftest import FakeSlackPort


def _build(
    factory: async_sessionmaker[AsyncSession],
    slack: FakeSlackPort,
    *,
    tech_assistance_channel_id: str | None = "C_TECH",
) -> OpenIntakeModal:
    return OpenIntakeModal(
        slack=slack,
        orgs=SQLiteOrgRepository(factory),
        drafts=SQLiteDraftFormSessionRepository(factory),
        tech_assistance_channel_id=tech_assistance_channel_id,
        csm_view_builder=csm_intake.build_view,
        se_view_builder=se_bug.build_view,
    )


@pytest.mark.asyncio
async def test_tech_assistance_invocation_opens_csm_intake(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    handler = _build(session_factory, fake_slack)
    await handler.execute(
        trigger_id="T1",
        invoker_user_id="U_CSM",
        invoker_channel_id="C_TECH",
    )
    assert len(fake_slack.views_opened) == 1
    _, view = fake_slack.views_opened[0]
    assert view["callback_id"] == csm_intake.CALLBACK_ID


@pytest.mark.asyncio
async def test_dm_invocation_opens_se_bug(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    handler = _build(session_factory, fake_slack)
    await handler.execute(
        trigger_id="T1",
        invoker_user_id="U_SE",
        invoker_channel_id="D_SOMEONE",  # not the configured tech-assistance channel
    )
    _, view = fake_slack.views_opened[0]
    assert view["callback_id"] == se_bug.CALLBACK_ID


@pytest.mark.asyncio
async def test_unconfigured_tech_assistance_falls_through_to_se(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    handler = _build(session_factory, fake_slack, tech_assistance_channel_id=None)
    await handler.execute(
        trigger_id="T1",
        invoker_user_id="U_CSM",
        invoker_channel_id="C_TECH",
    )
    _, view = fake_slack.views_opened[0]
    assert view["callback_id"] == se_bug.CALLBACK_ID


@pytest.mark.asyncio
async def test_writes_draft_session_with_30min_expiry(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    handler = _build(session_factory, fake_slack)
    await handler.execute(trigger_id="T1", invoker_user_id="U_SE", invoker_channel_id="D_SE")

    drafts = SQLiteDraftFormSessionRepository(session_factory)
    draft = await drafts.get_by_view_id(fake_slack.next_view_id)
    assert draft is not None
    assert draft.modal_kind == ModalKind.SE_BUG
    assert draft.invoker_user_id == "U_SE"
    elapsed = (draft.expires_at - draft.created_at).total_seconds()
    assert 60 * 29.5 <= elapsed <= 60 * 30.5  # ≈ 30 minutes


@pytest.mark.asyncio
async def test_empty_orgs_still_opens_modal_with_no_orgs_message(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Empty orgs table → fallback view explains the seeding requirement."""
    handler = _build(session_factory, fake_slack)
    await handler.execute(trigger_id="T1", invoker_user_id="U_SE", invoker_channel_id="D_SE")
    _, view = fake_slack.views_opened[0]
    # No submit button in the fallback view.
    assert "submit" not in view
    text = view["blocks"][0]["text"]["text"]
    assert "No customer orgs" in text


@pytest.mark.asyncio
async def test_no_draft_recorded_if_views_open_fails(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))

    async def open_view_failure(trigger_id: str, view: dict[str, object]) -> None:
        return None

    fake_slack.open_view = open_view_failure  # type: ignore[method-assign]

    handler = _build(session_factory, fake_slack)
    result = await handler.execute(
        trigger_id="T1", invoker_user_id="U_SE", invoker_channel_id="D_SE"
    )
    assert result is None

    drafts = SQLiteDraftFormSessionRepository(session_factory)
    assert await drafts.get_by_view_id(fake_slack.next_view_id) is None
