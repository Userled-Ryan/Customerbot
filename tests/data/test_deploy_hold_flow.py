"""Deploy hold, end-to-end against real SQLite.

A resolve fixed by a `userledio/core` PR keeps the customer "resolved" thread
reply (and the 🎫→✅ swap) until that PR is seen deploying in #engineering:
a release post listing it, then the GitHub Actions run link in that thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.support_threads import (
    IN_FLIGHT_REACTION,
    RESOLVED_REACTION,
    RESOLVED_THREAD_REPLY,
)
from customerbot.application.linear.inbound import LinearInboundEvent, LinearInboundHandler
from customerbot.application.tracking.deploy_hold import (
    ACTION_DEPLOY_HOLD_POST_NOW,
    DeployHoldNudgeJob,
    DeployHoldService,
)
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.reopen import ReopenTicket
from customerbot.application.tracking.resolve import ResolveTicket
from customerbot.data.repository.deploy_holds import (
    SQLiteDeployHoldRepository,
    SQLiteReleaseRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    ResolutionType,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeLinearPort, FakeSlackPort

ENGINEERING = "C_ENG"
SUPPORT = "C_SUPPORT"
THREAD_TS = "100.000001"


def _pr(n: int, repo: str = "userledio/core") -> str:
    return f"https://github.com/{repo}/pull/{n}"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class _H:
    factory: async_sessionmaker[AsyncSession]
    slack: FakeSlackPort
    tickets: SQLiteTicketRepository
    holds_repo: SQLiteDeployHoldRepository
    holds: DeployHoldService
    resolve: ResolveTicket
    ticket_id: int


async def _harness(
    factory: async_sessionmaker[AsyncSession], *, engineering: str | None = ENGINEERING
) -> _H:
    slack = FakeSlackPort()
    tickets = SQLiteTicketRepository(factory)
    events = SQLiteEventLogRepository(factory)
    orgs = SQLiteOrgRepository(factory)
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    created = await tickets.create(
        Ticket(
            title="checkout broken",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            status=TicketStatus.IN_PROGRESS,
            lane=Lane.DEV_ACTION,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            se_owner_user_id="U_OWNER",
            source=Source.CUSTOMER_CHANNEL,
        )
    )
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    await tickets.link_support_thread(
        created.id, SUPPORT, THREAD_TS, by_user_id="U_SE", now=_utcnow()
    )
    holds_repo = SQLiteDeployHoldRepository(factory)
    holds = DeployHoldService(
        tickets,
        orgs,
        slack,
        holds_repo,
        SQLiteReleaseRepository(factory),
        engineering_channel_id=engineering,
        support_channel_ids=(SUPPORT,),
    )
    resolve = ResolveTicket(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        se_user_id="U_SE",
        support_channel_ids=(SUPPORT,),
        deploy_holds=holds,
    )
    return _H(factory, slack, tickets, holds_repo, holds, resolve, created.id)


async def _resolve(h: _H, *prs: str) -> bool:
    result = await h.resolve.execute(
        ticket_id=h.ticket_id,
        by_user_id="U_SE",
        resolution_type=ResolutionType.CODE_CHANGE if prs else ResolutionType.NO_CODE_CHANGE,
        resolution_pr_link=prs[0] if prs else None,
        pr_links=prs,
    )
    return result.held_for_deploy


async def _release(h: _H, release_ts: str, *pr_numbers: int) -> None:
    lines = "\n".join(
        f"<@U1|Dev> abc1234 [PRO-1]: a change (#{n}) (1 hour ago)" for n in pr_numbers
    )
    text = f"These commits are about to be merged into release!\n\n---\n{lines}\n---"
    await h.holds.on_engineering_message(
        channel_id=ENGINEERING, ts=release_ts, thread_ts=release_ts, text=text
    )


async def _thread_reply(h: _H, release_ts: str, reply_ts: str, text: str) -> None:
    await h.holds.on_engineering_message(
        channel_id=ENGINEERING, ts=reply_ts, thread_ts=release_ts, text=text
    )


async def _run_link(h: _H, release_ts: str, reply_ts: str = "900.000009") -> None:
    url = "https://github.com/userledio/core/actions/runs/36429619765"
    await _thread_reply(h, release_ts, reply_ts, f"<{url}|github.com/userledio/core/…/36429619765>")


def _replies(slack: FakeSlackPort) -> list[tuple[str, str | None]]:
    return [(ch, ts) for ch, text, ts in slack.messages_sent if text == RESOLVED_THREAD_REPLY]


def _dm_texts(slack: FakeSlackPort, user_id: str) -> list[str]:
    return [
        block["text"]["text"]
        for uid, blocks, _ in slack.dm_blocks_sent
        if uid == user_id
        for block in blocks
        if block.get("type") == "section"
    ]


@pytest.mark.asyncio
async def test_core_pr_reply_held_until_run_link(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)

    assert await _resolve(h, _pr(9790)) is True
    ticket = await h.tickets.get(h.ticket_id)
    assert ticket is not None and ticket.status == TicketStatus.RESOLVED
    assert _replies(h.slack) == []
    assert h.slack.reactions_added == []
    assert any("not deployed yet" in t for t in _dm_texts(h.slack, "U_CSM"))

    # Release announced, deploy not started yet → still held.
    await _release(h, "800.000001", 9790, 9810)
    assert _replies(h.slack) == []

    # A chat reply in the thread with no run link doesn't release it either.
    await _thread_reply(h, "800.000001", "800.000002", "should TF be applied first?")
    assert _replies(h.slack) == []

    await _run_link(h, "800.000001")
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]
    assert (SUPPORT, THREAD_TS, IN_FLIGHT_REACTION) in h.slack.reactions_removed
    assert (SUPPORT, THREAD_TS, RESOLVED_REACTION) in h.slack.reactions_added
    assert any("is now live" in t for t in _dm_texts(h.slack, "U_CSM"))
    assert await h.holds_repo.list_open_for_ticket(h.ticket_id) == []

    # A second run link in the same thread doesn't double-post.
    await _run_link(h, "800.000001", "900.000010")
    assert len(_replies(h.slack)) == 1


@pytest.mark.asyncio
async def test_non_core_pr_posts_immediately(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(54, repo="userledio/customerbot")) is False
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]


@pytest.mark.asyncio
async def test_no_pr_posts_immediately(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h) is False
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]


@pytest.mark.asyncio
async def test_unconfigured_engineering_channel_posts_immediately(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory, engineering=None)
    assert await _resolve(h, _pr(9790)) is False
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]


@pytest.mark.asyncio
async def test_pr_already_deployed_posts_immediately(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Done moved late — after the release shipped — posts at resolve time."""
    h = await _harness(session_factory)
    await _release(h, "800.000001", 9790)
    await _run_link(h, "800.000001")

    assert await _resolve(h, _pr(9790)) is False
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]


@pytest.mark.asyncio
async def test_multi_pr_ticket_waits_for_every_pr(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9744), _pr(9765)) is True

    await _release(h, "800.000001", 9744)
    await _run_link(h, "800.000001")
    assert _replies(h.slack) == []

    await _release(h, "801.000001", 9765)
    await _run_link(h, "801.000001", "901.000001")
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]


@pytest.mark.asyncio
async def test_commits_queued_in_thread_ship_with_the_release(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9755)) is True

    await _release(h, "800.000001", 9720)
    await _thread_reply(
        h,
        "800.000001",
        "800.000002",
        "Queueing these for release as well:\n<@U1|Ade> 0b63a79f0 Stop marquee (#9755) (35 min)",
    )
    assert _replies(h.slack) == []
    await _run_link(h, "800.000001")
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]


@pytest.mark.asyncio
async def test_commits_queued_after_run_link_count_as_deployed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9780)) is True

    await _release(h, "800.000001", 9773)
    await _run_link(h, "800.000001")
    await _thread_reply(h, "800.000001", "800.000003", "<@U1|Henry> 21839298c fix (#9780) (4s)")
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]


@pytest.mark.asyncio
async def test_messages_outside_release_threads_are_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9790)) is True

    # Run link in a thread that isn't a release, and a release in another channel.
    await _run_link(h, "700.000001")
    await h.holds.on_engineering_message(
        channel_id="C_OTHER",
        ts="701.000001",
        thread_ts="701.000001",
        text="These commits are about to be merged into release!\n (#9790)",
    )
    assert _replies(h.slack) == []


@pytest.mark.asyncio
async def test_reopened_ticket_hold_is_cancelled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9790)) is True
    reopen = ReopenTicket(
        tickets=h.tickets,
        events=SQLiteEventLogRepository(h.factory),
        orgs=SQLiteOrgRepository(h.factory),
        slack=h.slack,
        se_user_id="U_SE",
    )
    await reopen.execute(ticket_id=h.ticket_id, by_user_id="U_SE")

    await _release(h, "800.000001", 9790)
    await _run_link(h, "800.000001")
    assert _replies(h.slack) == []
    assert await h.holds_repo.list_open_for_ticket(h.ticket_id) == []


@pytest.mark.asyncio
async def test_post_now_releases_without_deploy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9790)) is True

    assert await h.holds.post_now(ticket_id=h.ticket_id, by_user_id="U_OWNER") is True
    assert _replies(h.slack) == [(SUPPORT, THREAD_TS)]
    # Nothing left to post a second time.
    assert await h.holds.post_now(ticket_id=h.ticket_id, by_user_id="U_OWNER") is False
    assert len(_replies(h.slack)) == 1


@pytest.mark.asyncio
async def test_nudge_fires_once_after_three_days(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9790)) is True
    job = DeployHoldNudgeJob(h.tickets, h.slack, h.holds_repo, se_user_id="U_SE")
    h.slack.dm_blocks_sent.clear()

    now = _utcnow()
    assert await job.execute(now_utc=now + timedelta(days=2)) == 0
    assert await job.execute(now_utc=now + timedelta(days=3, minutes=1)) == 1
    [(owner, blocks, _)] = h.slack.dm_blocks_sent
    assert owner == "U_OWNER"
    assert "#9790" in blocks[0]["text"]["text"]
    button = blocks[1]["elements"][0]
    assert button["action_id"] == ACTION_DEPLOY_HOLD_POST_NOW
    assert button["value"] == str(h.ticket_id)
    # One nudge only — the reply is never auto-posted.
    assert await job.execute(now_utc=now + timedelta(days=5)) == 0
    assert _replies(h.slack) == []


@pytest.mark.asyncio
async def test_rehold_after_reopen_resets_nudge(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Re-resolving on the same PR restarts the hold (fresh 3-day clock)."""
    h = await _harness(session_factory)
    assert await _resolve(h, _pr(9790)) is True
    job = DeployHoldNudgeJob(h.tickets, h.slack, h.holds_repo, se_user_id="U_SE")
    now = _utcnow()
    assert await job.execute(now_utc=now + timedelta(days=4)) == 1

    reopen = ReopenTicket(
        tickets=h.tickets,
        events=SQLiteEventLogRepository(h.factory),
        orgs=SQLiteOrgRepository(h.factory),
        slack=h.slack,
        se_user_id="U_SE",
    )
    await reopen.execute(ticket_id=h.ticket_id, by_user_id="U_SE")
    assert await _resolve(h, _pr(9790)) is True
    [hold] = await h.holds_repo.list_open_for_ticket(h.ticket_id)
    assert hold.nudged_at is None


@pytest.mark.asyncio
async def test_linear_done_with_core_pr_holds_and_says_so(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.tickets.set_linear_issue(
        h.ticket_id, issue_id="iss-1", identifier="SUP-1", url="https://linear.app/x/SUP-1"
    )
    ticket = await h.tickets.get(h.ticket_id)
    assert ticket is not None
    linear = FakeLinearPort()
    linear.pr_links["iss-1"] = [_pr(9744), _pr(9765)]
    events = SQLiteEventLogRepository(h.factory)
    orgs = SQLiteOrgRepository(h.factory)
    inbound = LinearInboundHandler(
        tickets=h.tickets,
        events=events,
        orgs=orgs,
        slack=h.slack,
        drop_ticket=DropTicket(tickets=h.tickets, events=events, orgs=orgs, slack=h.slack),
        resolve_ticket=h.resolve,
        linear=linear,
        se_user_id="U_SE",
        actor_id="U_BOT",
        owner_notify_delay_seconds=0,
    )
    await inbound.handle(
        ticket,
        LinearInboundEvent(
            entity_type="Issue",
            actor_id="U_DEV",
            actor_name="Dana",
            issue_id="iss-1",
            new_state=LinearWorkflowState.DONE,
        ),
    )

    resolved = await h.tickets.get(h.ticket_id)
    assert resolved is not None and resolved.status == TicketStatus.RESOLVED
    assert resolved.resolution_pr_link == _pr(9744)
    assert _replies(h.slack) == []
    assert any("Merged but not deployed yet" in t for t in _dm_texts(h.slack, "U_CSM"))
    assert {hold.pr_number for hold in await h.holds_repo.list_open_for_ticket(h.ticket_id)} == {
        9744,
        9765,
    }
