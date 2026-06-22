"""Resolve-ticket lifecycle handlers (flow §6, §9c, plan Chunk 9).

Two related buttons:

- `Resolved` — moves the ticket to `Awaiting customer confirmation`. SE has
  shipped the fix / sent the answer; we're waiting for the customer to
  confirm. The 7-day auto-close (Chunk 8) takes it from there if no reply
  comes back.
- `Resolved via hotfix` — same status transition for the SE-facing ticket,
  PLUS auto-creates a paired *underlying-bug* ticket on the Dev Action
  lane so engineering still tracks the root cause. The two are linked via
  `ticket_links` with relation `hotfix-of` (new bug `hotfix-of` original).

Both DM SE the §9c draft customer-facing resolution summary; SE sends it
manually when ready. The bot never sends to customers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.comms_drafts import resolution as resolution_draft
from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import (
    Lane,
    TicketLinkRelation,
    TicketStatus,
    TicketSubtype,
    TicketType,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class ResolveResult:
    ticket: Ticket | None
    linked_bug: Ticket | None = None  # only set for hotfix path


class ResolveTicket:
    """Handle the `Resolved` and `Resolved via hotfix` button clicks."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._se_user_id = se_user_id
        self._linear = linear

    async def execute(
        self,
        *,
        ticket_id: int,
        by_user_id: str,
        via_hotfix: bool = False,
        sync_to_linear: bool = True,
    ) -> ResolveResult:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Resolve clicked on missing ticket %s", ticket_id)
            return ResolveResult(ticket=None)

        if ticket.status == TicketStatus.AWAITING_CUSTOMER:
            logger.info("Ticket %s already awaiting customer — no-op", ticket.display_id)
            return ResolveResult(ticket=ticket)

        now = _utcnow()
        prior_status = ticket.status
        await self._tickets.update_status(ticket.id, TicketStatus.AWAITING_CUSTOMER, now=now)
        await self._events.append_status_change(
            ticket_id=ticket.id,
            from_status=prior_status,
            to_status=TicketStatus.AWAITING_CUSTOMER,
            by_user_id=by_user_id,
            at=now,
            note="resolved-via-hotfix" if via_hotfix else "resolved",
        )

        linked_bug: Ticket | None = None
        if via_hotfix:
            linked_bug = await self._create_underlying_bug(ticket, by_user_id=by_user_id, now=now)

        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

        refreshed = await self._tickets.get(ticket.id)
        await self._dm_resolution_draft(refreshed or ticket, via_hotfix=via_hotfix)

        # Linear mirror: the SE-facing ticket is silently closed (Done) for
        # reporting. The hotfix's underlying bug becomes an open dev issue.
        # `sync_to_linear=False` when this is driven by an inbound Linear event,
        # so we never echo a write back to Linear.
        if sync_to_linear and self._linear is not None:
            await self._linear.mark_done_silently(ticket.id, state=LinearWorkflowState.DONE)
            if linked_bug is not None and linked_bug.id is not None:
                await self._linear.mirror_new_ticket(linked_bug)
                await self._linear.ensure_open_for_dev(linked_bug.id)

        return ResolveResult(ticket=refreshed, linked_bug=linked_bug)

    async def _create_underlying_bug(
        self, source: Ticket, *, by_user_id: str, now: datetime
    ) -> Ticket | None:
        assert source.id is not None
        # Inherit fields per flow §7c — keep priority + severity + feature +
        # affected user, but reset status, retitle as "Underlying bug:", flip
        # the lane to Dev Action.
        new_ticket = Ticket(
            title=f"Underlying bug: {source.title}"[:140],
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            status=TicketStatus.IN_PROGRESS,
            lane=Lane.DEV_ACTION,
            priority=source.priority,
            severity=source.severity,
            feature=source.feature,
            description=(
                f"Root-cause investigation for {source.display_id} "
                f"(hotfix delivered to customer).\n\n"
                f"{source.description}"
            )[:4000],
            reporter_user_id=by_user_id,
            source=source.source,
            original_slack_link=source.original_slack_link,
            prod_link=source.prod_link,
            replay_link=source.replay_link,
            screenshot_url=source.screenshot_url,
            created_at=now,
            updated_at=now,
        )
        created = await self._tickets.create(new_ticket)
        assert created.id is not None

        # Copy affected orgs onto the linked bug so the Dev Action lane sees
        # the same customer surface as the SE Action ticket.
        for org_id in await self._tickets.list_orgs(source.id):
            await self._tickets.add_org(created.id, org_id)

        # Record the linkage. `from = new bug`, `to = original`, relation =
        # hotfix-of: "new bug is a hotfix-of the original".
        await self._tickets.add_link(created.id, source.id, TicketLinkRelation.HOTFIX_OF)

        # null → New (then the In-progress we set on creation overrides via
        # the next event row). Keep the audit trail honest with two rows.
        await self._events.append_status_change(
            ticket_id=created.id,
            from_status=None,
            to_status=TicketStatus.NEW,
            by_user_id=by_user_id,
            at=now,
            note=f"auto-created as hotfix-of {source.display_id}",
        )
        await self._events.append_status_change(
            ticket_id=created.id,
            from_status=TicketStatus.NEW,
            to_status=TicketStatus.IN_PROGRESS,
            by_user_id=by_user_id,
            at=now,
            note="dev investigation started",
        )
        return created

    async def _dm_resolution_draft(self, ticket: Ticket, *, via_hotfix: bool) -> None:
        draft = resolution_draft(ticket, via_hotfix=via_hotfix)
        await self._slack.send_dm_blocks(
            self._se_user_id,
            draft.blocks(),
            text=f"Resolution draft: {ticket.display_id}",
        )
