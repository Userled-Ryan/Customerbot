"""Integration tests for `DetectLogCheck` against real SQLite repositories.

Covers the v1 behaviour: filtered triggers, internal-member gating, thread-
already-linked suppression, channel→org cache population, and DM-with-button
payload shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.detect_log_check import (
    OPEN_SE_BUG_FROM_DETECTOR,
    DetectLogCheck,
)
from customerbot.data.repository.bot_state import SQLiteChannelOrgCacheRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.messaging.ports import ThreadMessage
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeSlackPort


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _build(
    factory: async_sessionmaker[AsyncSession],
    slack: FakeSlackPort,
    *,
    bot_user_id: str | None = "U_BOT",
    internal_user_group_id: str | None = "S_INTERNAL",
) -> DetectLogCheck:
    return DetectLogCheck(
        slack=slack,
        orgs=SQLiteOrgRepository(factory),
        channel_org_cache=SQLiteChannelOrgCacheRepository(factory),
        tickets=SQLiteTicketRepository(factory),
        bot_user_id=bot_user_id,
        internal_user_group_id=internal_user_group_id,
    )


def _internal(fake_slack: FakeSlackPort, *user_ids: str) -> None:
    fake_slack.user_group_memberships["S_INTERNAL"] = set(user_ids)


@pytest.mark.asyncio
async def test_internal_member_log_message_dms_se(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    _internal(fake_slack, "U_SE")
    detector = _build(session_factory, fake_slack)

    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.123",
        sender_user_id="U_SE",
        text="Let me log this and investigate",
    )
    assert fired is True
    assert len(fake_slack.dm_blocks_sent) == 1
    user_id, blocks, _text = fake_slack.dm_blocks_sent[0]
    assert user_id == "U_SE"

    # Button carries the channel/thread/permalink/description payload.
    actions_block = next(b for b in blocks if b["type"] == "actions")
    button = actions_block["elements"][0]
    assert button["action_id"] == OPEN_SE_BUG_FROM_DETECTOR
    payload = json.loads(button["value"])
    assert payload["channel_id"] == "C_ACME"
    assert payload["thread_ts"] == "1700.123"
    assert payload["permalink"].endswith("/archives/C_ACME/p1700123")


@pytest.mark.asyncio
async def test_member_of_any_listed_group_fires(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    # Sender is in the second of three comma-separated groups.
    fake_slack.user_group_memberships["S_CS"] = set()
    fake_slack.user_group_memberships["S_SALES"] = {"U_SALES"}
    fake_slack.user_group_memberships["S_DEVS"] = set()
    detector = _build(session_factory, fake_slack, internal_user_group_id="S_CS, S_SALES, S_DEVS")
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_SALES",
        text="log this",
    )
    assert fired is True
    assert len(fake_slack.dm_blocks_sent) == 1


@pytest.mark.asyncio
async def test_member_of_no_listed_group_is_ignored(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    fake_slack.user_group_memberships["S_CS"] = {"U_CS"}
    fake_slack.user_group_memberships["S_DEVS"] = {"U_DEV"}
    detector = _build(session_factory, fake_slack, internal_user_group_id="S_CS,S_DEVS")
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_OUTSIDE",
        text="log this",
    )
    assert fired is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_non_internal_member_is_ignored(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    _internal(fake_slack, "U_SE")  # someone else, not the sender
    detector = _build(session_factory, fake_slack)
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_OUTSIDE",
        text="log this please",
    )
    assert fired is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_bot_self_message_is_ignored(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    _internal(fake_slack, "U_SE", "U_BOT")
    detector = _build(session_factory, fake_slack)
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_BOT",
        text="I'll log this",
    )
    assert fired is False


@pytest.mark.asyncio
async def test_unset_internal_group_disables_detector(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    detector = _build(session_factory, fake_slack, internal_user_group_id=None)
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_SE",
        text="log this",
    )
    assert fired is False


@pytest.mark.asyncio
async def test_no_log_negation_suppresses(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    _internal(fake_slack, "U_SE")
    detector = _build(session_factory, fake_slack)
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_SE",
        text="no log needed for this",
    )
    assert fired is False


@pytest.mark.asyncio
async def test_thread_already_linked_to_live_ticket_is_suppressed(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    _internal(fake_slack, "U_SE")
    # Seed a live ticket already linked to the thread.
    tickets = SQLiteTicketRepository(session_factory)
    permalink = "https://test.slack.com/archives/C_ACME/p1700123"
    await tickets.create(
        Ticket(
            title="x",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            original_slack_link=permalink,
        )
    )

    detector = _build(session_factory, fake_slack)
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.123",
        sender_user_id="U_SE",
        text="Let me log this",
    )
    assert fired is False
    assert fake_slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_thread_linked_to_closed_ticket_does_not_suppress(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """A previously-closed ticket on the same thread should not block a new one."""
    _internal(fake_slack, "U_SE")
    tickets = SQLiteTicketRepository(session_factory)
    permalink = "https://test.slack.com/archives/C_ACME/p1700123"
    t = await tickets.create(
        Ticket(
            title="x",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            original_slack_link=permalink,
        )
    )
    assert t.id is not None
    await tickets.update_status(t.id, TicketStatus.CLOSED, now=_utcnow())

    detector = _build(session_factory, fake_slack)
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.123",
        sender_user_id="U_SE",
        text="log this — recurring",
    )
    assert fired is True


@pytest.mark.asyncio
async def test_channel_org_cache_populated_on_miss_with_match(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    _internal(fake_slack, "U_SE")
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme", slack_channel_id="C_ACME"))

    detector = _build(session_factory, fake_slack)
    await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_SE",
        text="log this",
    )

    cache = SQLiteChannelOrgCacheRepository(session_factory)
    entry = await cache.get("C_ACME")
    assert entry is not None
    assert entry.org_id == "acme"

    button = next(b for b in fake_slack.dm_blocks_sent[0][1] if b["type"] == "actions")
    payload = json.loads(button["elements"][0]["value"])
    assert payload["org_id"] == "acme"


@pytest.mark.asyncio
async def test_channel_org_cache_populated_negative_when_no_match(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Channel without a configured org → cache an `org_id=None` entry to skip lookup next time."""
    _internal(fake_slack, "U_SE")
    detector = _build(session_factory, fake_slack)
    await detector.execute(
        channel_id="C_RANDOM",
        thread_ts="1700.1",
        sender_user_id="U_SE",
        text="log this",
    )

    cache = SQLiteChannelOrgCacheRepository(session_factory)
    entry = await cache.get("C_RANDOM")
    assert entry is not None
    assert entry.org_id is None


@pytest.mark.asyncio
async def test_description_drafted_from_last_5_thread_messages(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    _internal(fake_slack, "U_SE")
    fake_slack.thread_messages[("C_ACME", "1700.1")] = [
        ThreadMessage(user_id="U_CUST", text="Hi, our reports broke today"),
        ThreadMessage(user_id="U_SE", text="Looking now"),
        ThreadMessage(user_id="U_CUST", text="It's blocking the launch"),
    ]
    detector = _build(session_factory, fake_slack)
    await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_SE",
        text="let me log this",
    )

    button = next(b for b in fake_slack.dm_blocks_sent[0][1] if b["type"] == "actions")
    payload = json.loads(button["elements"][0]["value"])
    description = payload["description"]
    assert "reports broke today" in description
    assert "Looking now" in description
    assert "blocking the launch" in description


@pytest.mark.asyncio
async def test_logger_does_not_trigger(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
) -> None:
    """Word boundary check at the use-case level."""
    _internal(fake_slack, "U_SE")
    detector = _build(session_factory, fake_slack)
    fired = await detector.execute(
        channel_id="C_ACME",
        thread_ts="1700.1",
        sender_user_id="U_SE",
        text="Add a logger to the failing handler",
    )
    assert fired is False
