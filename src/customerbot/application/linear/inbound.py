"""LinearInboundHandler — apply a dev's Linear change back to customerbot.

Keeps the two sides in sync (the "no desync" requirement) and notifies the SE +
the ticket's stakeholders (the CSMs on the card) on every dev touch, as a
failover so nothing engineering does goes unseen.

Loop suppression (this handler is the inbound side of a two-way sync):
  1. It NEVER calls outbound `LinearSync`; status transitions are routed
     through `ResolveTicket`/`DropTicket` with `sync_to_linear=False`, so a
     Linear-originated change is never echoed back to Linear.
  2. Events whose actor is our own integration (`actor_id`) are dropped — that
     kills the webhook our own silent mark-done emits.
  3. Only `DEV_ACTION` tickets are acted on; SE-lane mirrors are silently
     closed by us and have no dev working them.
  4. Transitions are idempotent (the routed handlers no-op if already there),
     so Linear's at-least-once delivery is safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.tracking.drop import DropTicket
from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import Lane, TicketStatus
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
        se_user_id: str,
        actor_id: str | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._drop = drop_ticket
        self._se_user_id = se_user_id
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
        # (3) only dev-lane tickets have a dev working them in Linear.
        if ticket.lane != Lane.DEV_ACTION:
            logger.debug("Ignoring inbound Linear event on non-dev ticket %s", ticket.display_id)
            return

        who = event.actor_name or "a developer"

        if event.entity_type == "Comment":
            await self._notify(
                ticket, f":speech_balloon: {who} commented on {ticket.display_id} in Linear."
            )
            return

        if event.new_state is None:
            return

        # Each branch is guarded by current status so it's idempotent: Linear's
        # at-least-once retries (and the reconcile sweep, which reuses this same
        # path) never re-transition or re-notify a ticket already in sync.
        intent = linear_state_to_inbound_intent(event.new_state)
        if intent == InboundIntent.RESOLVE:
            # A dev finishing in Linear is *not* a terminal resolve — that stays
            # the SE's explicit, reporting-capturing `Resolved` click. We move
            # the ticket to Awaiting customer (SLA paused) and prompt the SE to
            # confirm with the customer, then click Resolved themselves.
            if ticket.status in (
                TicketStatus.AWAITING_CUSTOMER,
                TicketStatus.RESOLVED,
                TicketStatus.CLOSED,
            ):
                return
            await self._reflect_awaiting_customer(ticket)
            await self._notify(
                ticket,
                f":white_check_mark: {who} marked {ticket.display_id} *Done* in Linear — "
                f"confirm with the customer, then click *Resolved*.",
            )
        elif intent == InboundIntent.DROP:
            if ticket.status == TicketStatus.CLOSED:
                return
            await self._drop.execute(
                ticket_id=ticket.id, by_user_id=LINEAR_ACTOR, sync_to_linear=False
            )
            await self._notify(
                ticket, f":wastebasket: {who} canceled {ticket.display_id} in Linear."
            )
        elif intent == InboundIntent.REOPEN_IN_PROGRESS:
            if ticket.status == TicketStatus.IN_PROGRESS:
                return
            await self._reflect_in_progress(ticket)
            await self._notify(
                ticket, f":construction: {who} started work on {ticket.display_id} in Linear."
            )
        # InboundIntent.NONE (Triage / Awaiting) — no status change, no notify.

    async def _reflect_awaiting_customer(self, ticket: Ticket) -> None:
        assert ticket.id is not None
        now = _utcnow()
        prior = ticket.status
        await self._tickets.update_status(ticket.id, TicketStatus.AWAITING_CUSTOMER, now=now)
        await self._events.append_status_change(
            ticket_id=ticket.id,
            from_status=prior,
            to_status=TicketStatus.AWAITING_CUSTOMER,
            by_user_id=LINEAR_ACTOR,
            at=now,
            note="dev marked Done in Linear — awaiting customer confirmation",
        )
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

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
        """DM the SE + the ticket's stakeholder CSMs (deduped)."""
        recipients = [self._se_user_id]
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
