"""LinearInboundHandler — apply a dev's Linear change back to customerbot.

Keeps the two sides in sync (the "no desync" requirement) and notifies the SE +
the ticket's stakeholders (the CSMs on the card) on every dev touch, as a
failover so nothing engineering does goes unseen.

Both lanes are acted on: dev-lane issues (worked by engineers in the Product
Responder project) and SE-lane issues (worked by SEs directly in the SE
Responder Linear view). A status change on either side is mirrored back to the
customerbot ticket so nothing falls through the gap.

Loop suppression (this handler is the inbound side of a two-way sync):
  1. It NEVER calls outbound `LinearSync`; status transitions are routed
     through `ResolveTicket`/`DropTicket` with `sync_to_linear=False`, so a
     Linear-originated change is never echoed back to Linear.
  2. Events whose actor is our own integration (`actor_id`) are dropped — that
     kills the webhook our own silent mark-done emits (e.g. when an SE resolves
     from the Slack card and we close the SE-lane mirror).
  3. Transitions are idempotent (the routed handlers no-op if already there),
     so Linear's at-least-once delivery — and a self-echo that slips through
     when `actor_id` is unset — is safe.

Notifications differ by lane: dev-lane touches DM the SE (a failover so nothing
engineering does goes unseen) plus the CSMs; SE-lane touches DM the CSMs only —
the acting SE is the one making the change, mirroring the Slack `Resolved` flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.links import linked_display_id
from customerbot.application.tracking.resolve import ResolveTicket
from customerbot.domain.linear.ports import LinearPort, LinearWorkflowState
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import Lane, ResolutionType, TicketStatus
from customerbot.integration.linear.mapping import (
    InboundIntent,
    linear_state_to_inbound_intent,
)

logger = logging.getLogger(__name__)

# Audit marker written as `by_user_id` for Linear-originated transitions.
LINEAR_ACTOR = "linear"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class LinearInboundEvent:
    """Normalised view of a Linear webhook delivery (built by `LinearWebhook`)."""

    entity_type: str  # "Issue" | "Comment"
    actor_id: str | None
    actor_name: str | None
    issue_id: str
    new_state: LinearWorkflowState | None = None  # set for Issue state changes
    comment_body: str | None = None  # set for Comment creates


class LinearInboundHandler:
    def __init__(
        self,
        *,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        drop_ticket: DropTicket,
        resolve_ticket: ResolveTicket,
        linear: LinearPort,
        se_user_id: str,
        workspace_url: str = "",
        actor_id: str | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._drop = drop_ticket
        self._resolve = resolve_ticket
        self._linear = linear
        self._se_user_id = se_user_id
        self._workspace_url = workspace_url
        # Public so the resolved gateway actor id can be wired in at startup
        # (auto-resolution) when not pinned via config.
        self.actor_id = actor_id

    async def handle(self, ticket: Ticket, event: LinearInboundEvent) -> None:
        if ticket.id is None:
            return
        # (2) self-event filter — ignore webhooks our own writes triggered.
        if self.actor_id is not None and event.actor_id == self.actor_id:
            logger.debug("Ignoring self-actor Linear event for %s", ticket.display_id)
            return

        who = event.actor_name or "a developer"
        ref = linked_display_id(ticket, self._workspace_url)

        if event.entity_type == "Comment":
            # The comment failover exists so the SE sees engineering's replies on
            # a dev-lane issue; an SE commenting on their own SE-lane issue needs
            # no notification.
            if ticket.lane == Lane.DEV_ACTION:
                await self._notify(ticket, f":speech_balloon: {who} commented on {ref} in Linear.")
            return

        if event.new_state is None:
            return

        # Each branch is guarded by current status so it's idempotent: Linear's
        # at-least-once retries (and the reconcile sweep, which reuses this same
        # path) never re-transition or re-notify a ticket already in sync.
        intent = linear_state_to_inbound_intent(event.new_state)
        if intent == InboundIntent.RESOLVE:
            # Marking the issue Done in Linear resolves the ticket (terminal),
            # mirroring the SE's `Resolved` click — the SE reopens from the card
            # if the customer says it isn't fixed. The resolution is recorded as
            # a code change with the PR when one is linked on the issue, else as
            # a no-code-change resolve. Routed through `ResolveTicket` with
            # `sync_to_linear=False` so we never echo a write back to Linear (and
            # so its CSM alert stays off — our own `_notify` covers SE + CSMs).
            if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
                return
            pr_link = await self._linear.get_issue_pr_link(issue_id=event.issue_id)
            resolution_type = (
                ResolutionType.CODE_CHANGE if pr_link else ResolutionType.NO_CODE_CHANGE
            )
            await self._resolve.execute(
                ticket_id=ticket.id,
                by_user_id=LINEAR_ACTOR,
                resolution_type=resolution_type,
                resolution_pr_link=pr_link,
                sync_to_linear=False,
            )
            detail = f" (<{pr_link}|PR>)" if pr_link else ""
            await self._notify(
                ticket,
                f":white_check_mark: {who} marked {ref} *Done* in Linear — "
                f"ticket *resolved*{detail}. Reopen from the card if the customer "
                f"says otherwise.",
            )
        elif intent == InboundIntent.DROP:
            if ticket.status == TicketStatus.CLOSED:
                return
            await self._drop.execute(
                ticket_id=ticket.id, by_user_id=LINEAR_ACTOR, sync_to_linear=False
            )
            await self._notify(ticket, f":wastebasket: {who} canceled {ref} in Linear.")
        elif intent == InboundIntent.REOPEN_IN_PROGRESS:
            if ticket.status == TicketStatus.IN_PROGRESS:
                return
            await self._reflect_in_progress(ticket)
            await self._notify(ticket, f":construction: {who} started work on {ref} in Linear.")
        # InboundIntent.NONE (Triage / Awaiting) — no status change, no notify.

    async def _reflect_in_progress(self, ticket: Ticket) -> None:
        assert ticket.id is not None
        if ticket.status == TicketStatus.IN_PROGRESS:
            return
        now = _utcnow()
        prior = ticket.status
        await self._tickets.update_status(ticket.id, TicketStatus.IN_PROGRESS, now=now)
        await self._events.append_status_change(
            ticket_id=ticket.id,
            from_status=prior,
            to_status=TicketStatus.IN_PROGRESS,
            by_user_id=LINEAR_ACTOR,
            at=now,
            note="dev started work in Linear",
        )
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

    async def _notify(self, ticket: Ticket, text: str) -> None:
        """DM the ticket's stakeholder CSMs (deduped).

        Dev-lane touches also DM the SE — a failover so nothing engineering does
        goes unseen. SE-lane touches don't: the SE is the one making the change
        in Linear, so notifying them would just echo their own action back (this
        mirrors the Slack `Resolved` flow, which alerts CSMs, not the SE).
        """
        recipients = [self._se_user_id] if ticket.lane == Lane.DEV_ACTION else []
        for org_id in await self._tickets.list_orgs(ticket.id or 0):
            org = await self._orgs.get(org_id)
            if org is not None and org.csm_user_id and org.csm_user_id not in recipients:
                recipients.append(org.csm_user_id)
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
        if ticket.linear_issue_url:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"<{ticket.linear_issue_url}|Open in Linear>"}
                    ],
                }
            )
        for user_id in recipients:
            await self._slack.send_dm_blocks(user_id, blocks, text=text)
