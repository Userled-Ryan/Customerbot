from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.open_intake_modal import OpenIntakeModal
from customerbot.data.repository.bot_state import SQLiteDraftFormSessionRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.domain.bot_state.entities import ModalKind
from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import Source
from customerbot.integration.slack.handler import _shortcut_prefill
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
async def test_support_channel_invocation_opens_se_form(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """The per-channel split was retired: `#userled-support` now opens the same
    full SE intake form as everywhere else (the SE logs the ticket after the
    customer posts free text in the channel)."""
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
    assert view["callback_id"] == se_bug.CALLBACK_ID


def test_shortcut_prefill_wraps_message_under_divider() -> None:
    """Message text goes below a `----` divider with a blank line above it for
    the SE's own context; empty messages yield a blank form."""
    assert _shortcut_prefill("EU ads not showing") == "\n----\n\nEU ads not showing"
    assert _shortcut_prefill("  spaced  ") == "\n----\n\nspaced"
    assert _shortcut_prefill("") == ""
    assert _shortcut_prefill("   ") == ""


def test_initial_source_maps_invocation_context(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Source pre-select matches where /log was invoked: customer channel →
    Customer channel, support channel → #userled-support, DM (or no channel) →
    DM."""
    handler = _build(session_factory, fake_slack)  # tech_assistance_channel_id="C_TECH"
    assert handler._initial_source("C_CUSTOMER") == Source.CUSTOMER_CHANNEL
    assert handler._initial_source("C_TECH") == Source.TECH_ASSISTANCE
    assert handler._initial_source("D_SOMEONE") == Source.DM
    assert handler._initial_source(None) == Source.DM


@pytest.mark.asyncio
async def test_se_form_prefills_source_from_channel(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """End-to-end: opening from a customer channel pre-selects Customer channel
    on the rendered SE form."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    handler = _build(session_factory, fake_slack)

    await handler.execute(trigger_id="T1", invoker_user_id="U_SE", invoker_channel_id="C_CUSTOMER")
    _, view = fake_slack.views_opened[0]
    source_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_SOURCE)
    assert source_block["element"]["initial_option"]["value"] == Source.CUSTOMER_CHANNEL.value


@pytest.mark.asyncio
async def test_se_form_preselects_org_from_channel(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Invoking from an org's own Slack channel pre-selects that org."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", slack_channel_id="C_ACME"))
    await orgs.upsert(Org(id="globex", name="Globex"))
    handler = _build(session_factory, fake_slack)

    await handler.execute(trigger_id="T1", invoker_user_id="U_SE", invoker_channel_id="C_ACME")
    _, view = fake_slack.views_opened[0]
    org_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_ORG)
    assert org_block["element"]["initial_option"]["value"] == "acme"


@pytest.mark.asyncio
async def test_se_form_no_org_preselected_for_unmapped_channel(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A channel that matches no org leaves the org picker empty (not defaulted
    to some org whose channel is unset)."""
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))  # no slack_channel_id
    handler = _build(session_factory, fake_slack)

    await handler.execute(trigger_id="T1", invoker_user_id="U_SE", invoker_channel_id="C_RANDOM")
    _, view = fake_slack.views_opened[0]
    org_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_ORG)
    assert "initial_option" not in org_block["element"]


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


@pytest.mark.asyncio
async def test_toggle_new_org_reveals_and_hides_fields(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    handler = _build(session_factory, fake_slack)

    # Picking "Create new org…" reveals the inline fields via views.update,
    # with the owner picker defaulting to the person logging.
    await handler.toggle_new_org(
        view_id="V1",
        show_new_org=True,
        invoker_user_id="U_ME",
        state_values={},
        private_metadata="",
    )
    assert len(fake_slack.views_updated) == 1
    view_id, view = fake_slack.views_updated[0]
    assert view_id == "V1"
    block_ids = {b.get("block_id") for b in view["blocks"]}
    assert se_bug.BLOCK_NEW_ORG_CHANNEL in block_ids
    owner_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_NEW_ORG_OWNER)
    assert owner_block["element"]["initial_user"] == "U_ME"

    # Switching back to a real org hides them again.
    await handler.toggle_new_org(
        view_id="V1",
        show_new_org=False,
        invoker_user_id="U_ME",
        state_values={},
        private_metadata="",
    )
    _, view2 = fake_slack.views_updated[1]
    block_ids2 = {b.get("block_id") for b in view2["blocks"]}
    assert se_bug.BLOCK_NEW_ORG_CHANNEL not in block_ids2
