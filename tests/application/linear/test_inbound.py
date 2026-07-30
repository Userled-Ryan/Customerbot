"""LinearInboundHandler — applying dev Linear changes back into customerbot."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.linear.inbound import LinearInboundEvent, LinearInboundHandler
from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.resolve import ResolveTicket
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

ACTOR_BOT = "U_BOT"


def _bug(
    *, lane: Lane | None = Lane.DEV_ACTION, status: TicketStatus = TicketStatus.IN_PROGRESS
) -> Ticket:
    return Ticket(
        title="checkout broken",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        lane=lane,
        severity=Severity.BLOCKING,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="hang at submit",
        card_channel_id="C_SE_TICKETS",
        card_message_ts="1700000000.000100",
    )


class _Harness:
    def __init__(
        self,
        tickets: SQLiteTicketRepository,
        slack: FakeSlackPort,
        linear: FakeLinearPort,
        inbound: LinearInboundHandler,
        ticket: Ticket,
    ) -> None:
        self.tickets = tickets
        self.slack = slack
        self.linear = linear
        self.inbound = inbound
        self.ticket = ticket


async def _harness(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lane: Lane | None = Lane.DEV_ACTION,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    with_csm: bool = True,
    owner_notify_delay: float = 0.0,
) -> _Harness:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    slack = FakeSlackPort()
    fake_linear = FakeLinearPort()
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM" if with_csm else None))
    created = await tickets.create(_bug(lane=lane, status=status))
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)
    drop = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=slack, linear=sync)
    resolve = ResolveTicket(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        se_user_id="U_SE",
        linear=sync,
    )
    inbound = LinearInboundHandler(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        drop_ticket=drop,
        resolve_ticket=resolve,
        linear=fake_linear,
        se_user_id="U_SE",
        actor_id=ACTOR_BOT,
        owner_notify_delay_seconds=owner_notify_delay,
    )
    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    # Clear the mirror's side effects so assertions see only inbound activity.
    fake_linear.state_updates.clear()
    slack.dm_blocks_sent.clear()
    return _Harness(tickets, slack, fake_linear, inbound, refreshed)


def _issue_event(
    h: _Harness, state: LinearWorkflowState, *, actor: str = "U_DEV"
) -> LinearInboundEvent:
    return LinearInboundEvent(
        entity_type="Issue",
        actor_id=actor,
        actor_name="Dana",
        issue_id=h.ticket.linear_issue_id or "",
        new_state=state,
    )


def _assignee_event(
    h: _Harness, assignee_linear_id: str | None, *, actor: str = "U_DEV"
) -> LinearInboundEvent:
    return LinearInboundEvent(
        entity_type="Issue",
        actor_id=actor,
        actor_name="Dana",
        issue_id=h.ticket.linear_issue_id or "",
        assignee_changed=True,
        assignee_linear_id=assignee_linear_id,
    )


async def _flush_owner_dms(h: _Harness) -> None:
    """Drive any pending debounced owner-DM timers to completion.

    Await in isolation (no other DB work in flight) so the timer's own DB read
    doesn't interleave with the test's — mirroring production, where the fire
    happens minutes after the webhook, not concurrently with it.
    """
    for task in list(h.inbound._pending_owner_dm.values()):
        await task


def _cancel_owner_dms(h: _Harness) -> None:
    """Cancel any still-pending timers so they don't dangle past the test."""
    for task in list(h.inbound._pending_owner_dm.values()):
        task.cancel()


async def _set_owner(h: _Harness, owner: str | None) -> None:
    """Seed the ticket's SE owner in the DB and refresh the harness ticket."""
    assert h.ticket.id is not None
    await h.tickets.update_se_owner(h.ticket.id, owner, now=datetime(2026, 1, 1))
    refreshed = await h.tickets.get(h.ticket.id)
    assert refreshed is not None
    h.ticket = refreshed


async def _set_dev_owner(h: _Harness, owner: str | None) -> None:
    """Seed the ticket's dev owner — the field a dev-lane assignee maps onto."""
    assert h.ticket.id is not None
    await h.tickets.update_dev_owner(h.ticket.id, owner, now=datetime(2026, 1, 1))
    refreshed = await h.tickets.get(h.ticket.id)
    assert refreshed is not None
    h.ticket = refreshed


def _dm_recipients(h: _Harness) -> set[str]:
    return {uid for uid, _b, _t in h.slack.dm_blocks_sent}


async def _reload(h: _Harness) -> Ticket:
    """Reload the ticket as each webhook would — never reuse a stale object."""
    assert h.ticket.id is not None
    refreshed = await h.tickets.get(h.ticket.id)
    assert refreshed is not None
    return refreshed


@pytest.mark.asyncio
async def test_dev_done_resolves_without_echo(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None
    # Done in Linear is now a terminal resolve, mirroring the SE's Resolved click.
    assert updated.status == TicketStatus.RESOLVED
    # No PR linked → recorded as a no-code-change resolve.
    assert updated.resolution_type == ResolutionType.NO_CODE_CHANGE
    assert updated.resolution_pr_link is None
    # SE + stakeholder CSM both notified.
    recipients = {uid for uid, _b, _t in h.slack.dm_blocks_sent}
    assert "U_SE" in recipients
    assert "U_CSM" in recipients
    # No echo back to Linear (inbound transition uses sync_to_linear=False).
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_dev_done_with_pr_records_code_change(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    pr = "https://github.com/acme/app/pull/7"
    h.linear.pr_links[h.ticket.linear_issue_id or ""] = pr
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None
    assert updated.status == TicketStatus.RESOLVED
    # A linked PR → recorded as a code change carrying the PR link.
    assert updated.resolution_type == ResolutionType.CODE_CHANGE
    assert updated.resolution_pr_link == pr
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_dev_canceled_closes_ticket(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.CANCELED))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.CLOSED
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_comment_notifies_only_no_status_change(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    event = LinearInboundEvent(
        entity_type="Comment",
        actor_id="U_DEV",
        actor_name="Dana",
        issue_id=h.ticket.linear_issue_id or "",
        comment_body="looking into it",
    )
    await h.inbound.handle(h.ticket, event)

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS  # unchanged
    assert {uid for uid, _b, _t in h.slack.dm_blocks_sent} == {"U_SE", "U_CSM"}


@pytest.mark.asyncio
async def test_self_actor_event_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE, actor=ACTOR_BOT))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS  # unchanged
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_se_lane_done_resolves_csm_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # An SE moving their own SE Responder issue to Done resolves the ticket in
    # Slack — parity with the SE's own Resolved click.
    h = await _harness(session_factory, lane=Lane.SE_ACTION)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None
    assert updated.status == TicketStatus.RESOLVED
    assert updated.resolution_type == ResolutionType.NO_CODE_CHANGE
    # SE-lane change notifies the stakeholder CSM only — not the acting SE.
    recipients = {uid for uid, _b, _t in h.slack.dm_blocks_sent}
    assert recipients == {"U_CSM"}
    # No echo back to Linear.
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_se_lane_canceled_closes_csm_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory, lane=Lane.SE_ACTION)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.CANCELED))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.CLOSED
    assert {uid for uid, _b, _t in h.slack.dm_blocks_sent} == {"U_CSM"}
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_se_lane_started_reflects_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # SE-lane ticket sitting in Awaiting customer; SE reopens work in Linear.
    h = await _harness(session_factory, lane=Lane.SE_ACTION, status=TicketStatus.AWAITING_CUSTOMER)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.IN_PROGRESS))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS
    assert {uid for uid, _b, _t in h.slack.dm_blocks_sent} == {"U_CSM"}
    assert h.linear.state_updates == []


@pytest.mark.asyncio
async def test_se_lane_comment_notifies_no_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A comment on an SE-lane issue (the SE talking to themselves) DMs no one.
    h = await _harness(session_factory, lane=Lane.SE_ACTION)
    event = LinearInboundEvent(
        entity_type="Comment",
        actor_id="U_SE",
        actor_name="Sam",
        issue_id=h.ticket.linear_issue_id or "",
        comment_body="still digging",
    )
    await h.inbound.handle(h.ticket, event)

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS  # unchanged
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_se_lane_self_actor_event_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Our own mark-done write on the SE-lane mirror (when the SE resolves from
    # the Slack card) must not loop back into a redundant resolve.
    h = await _harness(session_factory, lane=Lane.SE_ACTION)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE, actor=ACTOR_BOT))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS  # unchanged
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_done_is_idempotent_no_double_notify(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory)
    await h.inbound.handle(h.ticket, _issue_event(h, LinearWorkflowState.DONE))
    sent_after_first = len(h.slack.dm_blocks_sent)
    # Re-deliver the same Done (Linear retry / reconcile) — must be a no-op now.
    refreshed = await h.tickets.get(h.ticket.id or 0)
    assert refreshed is not None
    await h.inbound.handle(refreshed, _issue_event(h, LinearWorkflowState.DONE))
    assert len(h.slack.dm_blocks_sent) == sent_after_first


# -- assignee (owner) mirroring, inbound ------------------------------------
#
# Which field a Linear assignee maps onto depends on the lane: dev-lane issues
# are engineering passing work between themselves, so they land on the *dev
# owner*; SE-lane issues land on the SE owner as before. The default harness
# ticket is on the dev lane.


@pytest.mark.asyncio
async def test_linear_assignee_updates_dev_owner_on_dev_lane(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory, owner_notify_delay=0.0)
    await _set_owner(h, "U_SE_OWNER")
    h.linear.linear_to_slack = {"lin_new": "U_NEW_DEV"}
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_new"))

    # Once the debounce settles, the new owner + the stakeholder CSM are DM'd.
    await _flush_owner_dms(h)
    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.dev_owner_user_id == "U_NEW_DEV"
    # The SE side is untouched — the card keeps showing who owns the customer.
    assert updated.se_owner_user_id == "U_SE_OWNER"
    assert _dm_recipients(h) == {"U_NEW_DEV", "U_CSM"}


@pytest.mark.asyncio
async def test_linear_assignee_updates_se_owner_on_se_lane(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory, lane=Lane.SE_ACTION, owner_notify_delay=0.0)
    h.linear.linear_to_slack = {"lin_new": "U_NEW_SE"}
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_new"))

    await _flush_owner_dms(h)
    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.se_owner_user_id == "U_NEW_SE"
    assert updated.dev_owner_user_id is None
    assert _dm_recipients(h) == {"U_NEW_SE", "U_CSM"}


@pytest.mark.asyncio
async def test_assignee_dm_is_debounced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # With a live delay, the DB/card update immediately but the DM waits.
    h = await _harness(session_factory, owner_notify_delay=60.0)
    h.linear.linear_to_slack = {"lin_new": "U_NEW_DEV"}
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_new"))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.dev_owner_user_id == "U_NEW_DEV"
    assert h.slack.dm_blocks_sent == []  # debounced — nothing sent yet
    assert len(h.inbound._pending_owner_dm) == 1
    _cancel_owner_dms(h)


@pytest.mark.asyncio
async def test_linear_unassign_clears_dev_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory, owner_notify_delay=0.0)
    await _set_dev_owner(h, "U_OLD_DEV")
    await h.inbound.handle(h.ticket, _assignee_event(h, None))

    await _flush_owner_dms(h)
    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.dev_owner_user_id is None
    # No owner to DM — only the stakeholder CSM hears about it.
    assert _dm_recipients(h) == {"U_CSM"}


@pytest.mark.asyncio
async def test_unmapped_linear_assignee_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Assigned in Linear to someone with no Slack mapping — leave the owner as-is.
    h = await _harness(session_factory, owner_notify_delay=0.0)
    await _set_dev_owner(h, "U_OLD_DEV")
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_unknown"))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.dev_owner_user_id == "U_OLD_DEV"  # unchanged
    assert h.inbound._pending_owner_dm == {}
    await _flush_owner_dms(h)
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_assignee_equal_current_owner_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    h = await _harness(session_factory, owner_notify_delay=0.0)
    await _set_dev_owner(h, "U_SAME")
    h.linear.linear_to_slack = {"lin_same": "U_SAME"}
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_same"))

    assert h.inbound._pending_owner_dm == {}
    await _flush_owner_dms(h)
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_assignee_self_actor_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Our own assign_issue write echoes back as a webhook — must be dropped.
    h = await _harness(session_factory, owner_notify_delay=0.0)
    h.linear.linear_to_slack = {"lin_new": "U_NEW_DEV"}
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_new", actor=ACTOR_BOT))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.dev_owner_user_id is None  # unchanged
    assert h.inbound._pending_owner_dm == {}
    assert h.slack.dm_blocks_sent == []


@pytest.mark.asyncio
async def test_assignee_dm_coalesces_a_burst(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Rapid reassignment A→B: the card tracks each change live, but only one
    # timer is ever pending and no DM fires mid-burst.
    h = await _harness(session_factory, owner_notify_delay=60.0)
    h.linear.linear_to_slack = {"lin_a": "U_A", "lin_b": "U_B"}
    # Each webhook loads a fresh ticket, so refetch between changes.
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_a"))
    h.ticket = await _reload(h)
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_b"))

    updated = await h.tickets.get(h.ticket.id or 0)
    assert updated is not None and updated.dev_owner_user_id == "U_B"  # final owner
    assert len(h.inbound._pending_owner_dm) == 1  # coalesced into one timer
    assert h.slack.dm_blocks_sent == []  # nothing sent mid-burst
    _cancel_owner_dms(h)


@pytest.mark.asyncio
async def test_assignee_dm_net_noop_skipped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Churns away and back to the pre-burst owner — the settled state is
    # unchanged, so firing the timer sends no DM.
    h = await _harness(session_factory, owner_notify_delay=60.0)
    await _set_dev_owner(h, "U_A")
    h.linear.linear_to_slack = {"lin_a": "U_A", "lin_b": "U_B"}
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_b"))  # A → B
    h.ticket = await _reload(h)
    await h.inbound.handle(h.ticket, _assignee_event(h, "lin_a"))  # B → A

    tid = h.ticket.id or 0
    origin = h.inbound._owner_burst_origin[tid]
    assert origin == "U_A"  # burst origin recorded once, at the first change
    _cancel_owner_dms(h)
    # Firing now (owner settled back on the origin) sends nothing.
    await h.inbound._fire_owner_dm(tid, origin, "Dana", "Bosh-001")
    assert h.slack.dm_blocks_sent == []
