"""Reply-needed flag + daily 5pm digest (Option B).

Covers:
- `ToggleReplyNeeded` flips the persisted flag and re-renders the card.
- `ReplyNeededDigestJob` only DMs inside the 17:00 SE-local hour, once per
  local day, listing only live tickets still flagged `reply_needed`.
- `render_reply_digest_blocks` links the original thread when present.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.reply_digest import (
    ReplyNeededDigestJob,
    render_reply_digest_blocks,
)
from customerbot.application.tracking.reply_needed import ToggleReplyNeeded
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort

# 2026-06-24, 17:30 UTC is inside the 17:00 fire hour for a UTC SE timezone.
_FIRE_WINDOW_UTC = datetime(2026, 6, 24, 17, 30)
_BEFORE_WINDOW_UTC = datetime(2026, 6, 24, 9, 0)


def _bug(
    *,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    reply_needed: bool = False,
    title: str = "checkout broken",
    original_slack_link: str | None = None,
) -> Ticket:
    return Ticket(
        title=title,
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        lane=Lane.SE_ACTION,
        priority=Priority.P2,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="users hang on submit",
        reply_needed=reply_needed,
        original_slack_link=original_slack_link,
    )


# --- ToggleReplyNeeded ------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_sets_then_clears_flag_and_refreshes_card(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    tickets = SQLiteTicketRepository(session_factory)
    created = await tickets.create(_bug())
    assert created.id is not None
    # A card must exist for refresh_card to update it.
    await tickets.update_card_message(created.id, "C_SE_TICKETS", "111.222")

    toggle = ToggleReplyNeeded(slack=fake_slack, tickets=tickets, orgs=orgs)

    after_on = await toggle.execute(ticket_id=created.id, by_user_id="U_SE")
    assert after_on is not None and after_on.reply_needed is True
    assert (await tickets.get(created.id)).reply_needed is True  # type: ignore[union-attr]

    after_off = await toggle.execute(ticket_id=created.id, by_user_id="U_SE")
    assert after_off is not None and after_off.reply_needed is False
    # The card was re-rendered each toggle (chat.update via FakeSlackPort).
    assert len(fake_slack.messages_updated) == 2


@pytest.mark.asyncio
async def test_toggle_missing_ticket_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    orgs = SQLiteOrgRepository(session_factory)
    tickets = SQLiteTicketRepository(session_factory)
    toggle = ToggleReplyNeeded(slack=fake_slack, tickets=tickets, orgs=orgs)
    assert await toggle.execute(ticket_id=999, by_user_id="U_SE") is None


# --- ReplyNeededDigestJob ---------------------------------------------------


def _job(tickets: SQLiteTicketRepository, fake_slack: FakeSlackPort) -> ReplyNeededDigestJob:
    return ReplyNeededDigestJob(
        tickets=tickets,
        slack=fake_slack,
        se_user_id="U_SE",
        se_timezone="UTC",
        workspace_url="https://test.slack.com",
    )


@pytest.mark.asyncio
async def test_digest_does_not_fire_outside_5pm(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_bug(reply_needed=True))
    fired = await _job(tickets, fake_slack).execute(now_utc=_BEFORE_WINDOW_UTC)
    assert fired is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_digest_stays_quiet_when_nothing_flagged(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_bug(reply_needed=False))
    fired = await _job(tickets, fake_slack).execute(now_utc=_FIRE_WINDOW_UTC)
    assert fired is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_digest_fires_at_5pm_then_throttles_same_day(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await tickets.create(_bug(reply_needed=True, title="A"))
    await tickets.create(_bug(reply_needed=True, title="B"))
    await tickets.create(_bug(reply_needed=False, title="C"))  # excluded
    # Closed-but-flagged must not appear.
    await tickets.create(_bug(reply_needed=True, status=TicketStatus.CLOSED, title="D"))

    job = _job(tickets, fake_slack)
    assert await job.execute(now_utc=_FIRE_WINDOW_UTC) is True
    assert len(fake_slack.dm_blocks_sent) == 1
    user, _blocks, text = fake_slack.dm_blocks_sent[0]
    assert user == "U_SE"
    assert "2 ticket(s)" in text

    # Second tick in the same 5pm hour is throttled.
    assert await job.execute(now_utc=datetime(2026, 6, 24, 17, 45)) is False
    assert len(fake_slack.dm_blocks_sent) == 1


def test_render_links_thread_when_present() -> None:
    with_link = _bug(reply_needed=True, original_slack_link="https://x.slack.com/p1")
    without = _bug(reply_needed=True, original_slack_link=None)
    rendered = "\n".join(
        b.get("text", {}).get("text", "")
        for b in render_reply_digest_blocks(
            [with_link, without], workspace_url="https://test.slack.com"
        )
    )
    assert "https://x.slack.com/p1|TIC-" in rendered
    assert "still waiting on a reply" in rendered
