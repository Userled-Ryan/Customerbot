"""Deploy hold — keep the customer "resolved" reply until the PR has shipped.

A Linear issue moves to Done when its PR *merges*, but customers only get the
fix once the next release *deploys*. Posting "this has been marked as resolved"
at merge time had CSMs and customers acting on a fix that wasn't live yet.

So when a resolve carries a PR in the watched repo (`userledio/core`), the
ticket still goes to Resolved internally — card retired, reminders stopped —
but the thread reply + 🎫→✅ swap is *held* until that PR is seen in a release
thread in #engineering that has its GitHub Actions run link posted. That link
is the deploy signal (it goes up when the deploy is kicked off); no GitHub API
is consulted.

- A ticket with several PRs is released only once all of them have shipped.
- A PR already in a deployed release when the ticket resolves posts at once.
- A ticket that has left Resolved by the time its deploy lands (reopened,
  dropped) has its hold cancelled — nothing is posted.
- A hold still waiting after `NUDGE_AFTER` DMs the ticket's SE with a button to
  post the reply anyway (`DeployHoldNudgeJob`); it never auto-posts.

Everything here is best-effort and inert when no engineering channel is
configured: `hold_or_post` then just posts, exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from customerbot.application.intake.support_threads import (
    IN_FLIGHT_REACTION,
    RESOLVED_REACTION,
    RESOLVED_THREAD_REPLY,
    collect_threads,
)
from customerbot.application.intake.ticket_card import csm_user_ids
from customerbot.application.tracking.links import linked_display_id
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    DeployHoldRepositoryPort,
    OrgRepositoryPort,
    ReleaseRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.releases import (
    DeployHold,
    is_release_announcement,
    parse_pr_numbers,
    parse_run_url,
    pr_number_from_url,
)
from customerbot.domain.tickets.value_objects import TicketStatus

logger = logging.getLogger(__name__)

DEFAULT_WATCH_REPO = "userledio/core"
NUDGE_AFTER = timedelta(days=3)
ACTION_DEPLOY_HOLD_POST_NOW = "deploy_hold_post_now"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DeployHoldService:
    def __init__(
        self,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        holds: DeployHoldRepositoryPort | None = None,
        releases: ReleaseRepositoryPort | None = None,
        *,
        engineering_channel_id: str | None = None,
        watch_repo: str = DEFAULT_WATCH_REPO,
        support_channel_ids: Collection[str] = (),
        workspace_url: str = "",
    ) -> None:
        self._tickets = tickets
        self._orgs = orgs
        self._slack = slack
        self._holds = holds
        self._releases = releases
        # Holding needs both stores and a channel to watch; otherwise every
        # resolve posts immediately, exactly as before this feature.
        self.engineering_channel_id = (
            engineering_channel_id if holds is not None and releases is not None else None
        )
        self._repo = watch_repo
        self._support_channel_ids = support_channel_ids
        self._workspace_url = workspace_url

    # --- resolve time -------------------------------------------------------

    async def hold_or_post(self, ticket: Ticket, pr_urls: Sequence[str]) -> bool:
        """Post the resolved reply now, or hold it for the deploy. True = held."""
        watched: dict[int, str] = {}
        for url in pr_urls:
            number = pr_number_from_url(url, self._repo)
            if number is not None:
                watched.setdefault(number, url)
        if ticket.id is None or not self.engineering_channel_id or not watched:
            await self.post_resolved(ticket)
            return False
        assert self._holds is not None and self._releases is not None
        pending = set(watched) - await self._releases.deployed_prs(watched.keys())
        if not pending:
            await self.post_resolved(ticket)
            return False
        now = _utcnow()
        for number in sorted(pending):
            await self._holds.add(ticket.id, number, watched[number], now=now)
        logger.info(
            "Holding resolved reply for %s until PR(s) %s deploy",
            ticket.display_id,
            ", ".join(f"#{n}" for n in sorted(pending)),
        )
        return True

    async def post_resolved(self, ticket: Ticket) -> None:
        """Close the loop in every attached thread: reply that it's resolved and
        swap the 🎫 in-flight reaction for ✅. Best-effort."""
        for channel_id, thread_ts in await collect_threads(
            self._tickets, self._slack, ticket, self._support_channel_ids
        ):
            await self._slack.send_message(channel_id, RESOLVED_THREAD_REPLY, thread_ts=thread_ts)
            await self._slack.remove_reaction(channel_id, thread_ts, IN_FLIGHT_REACTION)
            await self._slack.add_reaction(channel_id, thread_ts, RESOLVED_REACTION)

    # --- #engineering watcher -----------------------------------------------

    async def on_engineering_message(
        self, *, channel_id: str, ts: str, thread_ts: str, text: str
    ) -> None:
        """Track release threads; release holds once a run link lands."""
        if not self.engineering_channel_id or channel_id != self.engineering_channel_id:
            return
        assert self._releases is not None
        now = _utcnow()
        if thread_ts == ts:
            if is_release_announcement(text):
                await self._releases.upsert(ts, channel_id, now=now)
                await self._releases.add_prs(ts, parse_pr_numbers(text))
            return

        release = await self._releases.get(thread_ts)
        if release is None:
            return
        # Replies may queue more commits ("Queueing these for release as well:").
        added = parse_pr_numbers(text)
        await self._releases.add_prs(thread_ts, added)
        if release.deployed_at is None:
            run_url = parse_run_url(text, self._repo)
            if run_url is None:
                return
            await self._releases.mark_deployed(thread_ts, run_url, now=now)
            shipped = await self._releases.list_prs(thread_ts)
            logger.info("Release %s deployed (%s) — %d PR(s)", thread_ts, run_url, len(shipped))
        else:
            # Commits queued after the run link ride the same deploy.
            shipped = added
        await self._release_prs(shipped, now=now)

    async def _release_prs(self, pr_numbers: Collection[int], *, now: datetime) -> None:
        assert self._holds is not None
        released = 0
        for ticket_id in await self._holds.mark_deployed(pr_numbers, now=now):
            open_holds = await self._holds.list_open_for_ticket(ticket_id)
            if any(h.deployed_at is None for h in open_holds):
                continue  # another of the ticket's PRs hasn't shipped yet
            if await self._release_ticket(ticket_id, now=now):
                released += 1
        if released:
            logger.info("Released %d held resolved repl(ies)", released)

    # --- manual override (nudge button) -------------------------------------

    async def post_now(self, *, ticket_id: int, by_user_id: str) -> bool:
        """Post a held reply without waiting for the deploy. True if posted."""
        if self._holds is None or not await self._holds.list_open_for_ticket(ticket_id):
            return False
        logger.info("Deploy hold on ticket %s released manually by %s", ticket_id, by_user_id)
        return await self._release_ticket(ticket_id, now=_utcnow())

    async def _release_ticket(self, ticket_id: int, *, now: datetime) -> bool:
        assert self._holds is not None
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.status != TicketStatus.RESOLVED:
            # Reopened or dropped while waiting — the resolved reply no longer
            # applies. Lazy: nothing else has to remember to cancel holds.
            await self._holds.close_for_ticket(ticket_id, released=False, now=now)
            return False
        await self.post_resolved(ticket)
        await self._holds.close_for_ticket(ticket_id, released=True, now=now)
        ref = linked_display_id(ticket, self._workspace_url)
        text = f":rocket: *{ref} · {ticket.title}* is now live — customer thread updated."
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
        for csm_id in await csm_user_ids(self._tickets, self._orgs, ticket):
            await self._slack.send_dm_blocks(csm_id, blocks, text=f"{ticket.display_id} is live")
        return True


def nudge_blocks(
    ticket: Ticket, holds: Sequence[DeployHold], *, workspace_url: str, age_days: int
) -> list[dict[str, Any]]:
    assert ticket.id is not None
    ref = linked_display_id(ticket, workspace_url)
    prs = ", ".join(f"<{h.pr_url}|#{h.pr_number}>" for h in holds)
    plural = "s" if len(holds) > 1 else ""
    text = (
        f":hourglass: *{ref} · {ticket.title}* — PR{plural} {prs} merged {age_days} days "
        "ago but I haven't seen it in a release in #engineering. The customer thread "
        "still hasn't been told it's resolved."
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Post resolved reply now"},
                    "action_id": ACTION_DEPLOY_HOLD_POST_NOW,
                    "value": str(ticket.id),
                }
            ],
        },
    ]


class DeployHoldNudgeJob:
    """Scheduled job — DM the SE once about a hold still waiting after 3 days."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        slack: SlackPort,
        holds: DeployHoldRepositoryPort,
        *,
        se_user_id: str,
        workspace_url: str = "",
        nudge_after: timedelta = NUDGE_AFTER,
    ) -> None:
        self._tickets = tickets
        self._slack = slack
        self._holds = holds
        self._se_user_id = se_user_id
        self._workspace_url = workspace_url
        self._nudge_after = nudge_after

    async def execute(self, *, now_utc: datetime | None = None) -> int:
        """Return the number of nudge DMs sent this tick."""
        now = now_utc or _utcnow()
        by_ticket: dict[int, list[DeployHold]] = defaultdict(list)
        for hold in await self._holds.list_due_for_nudge(created_before=now - self._nudge_after):
            by_ticket[hold.ticket_id].append(hold)
        sent = 0
        for ticket_id, holds in by_ticket.items():
            ticket = await self._tickets.get(ticket_id)
            if ticket is None or ticket.status != TicketStatus.RESOLVED:
                await self._holds.close_for_ticket(ticket_id, released=False, now=now)
                continue
            age_days = (now - min(h.created_at for h in holds)).days
            owner = ticket.se_owner_user_id or self._se_user_id
            await self._slack.send_dm_blocks(
                owner,
                nudge_blocks(ticket, holds, workspace_url=self._workspace_url, age_days=age_days),
                text=f"{ticket.display_id} is still waiting on a deploy",
            )
            # Mark before the next tick regardless of DM success — one nudge only.
            await self._holds.mark_nudged(ticket_id, now=now)
            sent += 1
        return sent

    async def run_loop(self, *, interval_seconds: int = 900) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Deploy-hold nudge tick failed")
            await asyncio.sleep(interval_seconds)
