"""Resolve-ticket lifecycle (the `Resolved` ticket-card button).

`Resolved` is **terminal** (plan Part 2): the SE has confirmed the fix is
done, so the ticket goes straight to `RESOLVED`, the card retires, every
reminder stops, and the customer's CSM is DM'd. There's no 7-day
"awaiting customer" wait and no auto-created underlying-bug ticket — routing
to engineering stays an explicit "Move to Dev Action" click.

Clicking `Resolved` opens a small modal (`OpenResolveModal`) that captures
*how* it was resolved for reporting — `No code change` or `Code change`
(+ optional PR link). The submission handler then calls `ResolveTicket`.

Resolving is terminal and done-by-intent: the SE handles any customer
message themselves, so `ResolveTicket` no longer DMs the §9c draft
customer-facing resolution summary (it read as noise on an already-closed
ticket). The card is retired, the CSM is alerted, and the Linear mirror is
closed for reporting. The bot never messages customers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from customerbot.application.intake.ticket_card import notify_csms_status_change, refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import ResolutionType, TicketStatus

logger = logging.getLogger(__name__)

_RESOLUTION_LABEL: dict[ResolutionType, str] = {
    ResolutionType.NO_CODE_CHANGE: "No code change",
    ResolutionType.CODE_CHANGE: "Code change",
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class ResolveResult:
    ticket: Ticket | None


# `view_builder(ticket_id) -> view JSON`.
ResolveViewBuilder = Callable[..., dict[str, Any]]


class OpenResolveModal:
    """Open the resolve modal in response to the `Resolved` click."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        view_builder: ResolveViewBuilder,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._view_builder = view_builder

    async def execute(self, *, trigger_id: str, ticket_id: int) -> str | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None:
            logger.warning("Resolve clicked on missing ticket %s", ticket_id)
            return None
        view = self._view_builder(ticket_id=ticket_id)
        return await self._slack.open_view(trigger_id, view)


class ResolveTicket:
    """Mark a ticket Resolved (terminal) and capture how it was resolved."""

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
        resolution_type: ResolutionType,
        resolution_pr_link: str | None = None,
        sync_to_linear: bool = True,
    ) -> ResolveResult:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Resolve clicked on missing ticket %s", ticket_id)
            return ResolveResult(ticket=None)

        if ticket.status == TicketStatus.RESOLVED:
            logger.info("Ticket %s already resolved — no-op", ticket.display_id)
            return ResolveResult(ticket=ticket)

        now = _utcnow()
        prior_status = ticket.status
        await self._tickets.update_status(ticket.id, TicketStatus.RESOLVED, now=now)
        await self._tickets.set_resolution(ticket.id, resolution_type, resolution_pr_link, now=now)
        note = f"resolved ({resolution_type.value})"
        if resolution_pr_link:
            note += f" — {resolution_pr_link}"
        await self._events.append_status_change(
            ticket_id=ticket.id,
            from_status=prior_status,
            to_status=TicketStatus.RESOLVED,
            by_user_id=by_user_id,
            at=now,
            note=note,
        )

        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

        refreshed = await self._tickets.get(ticket.id)

        # CSM alert — only for SE-initiated resolves. When this is driven by an
        # inbound Linear "Done" (`sync_to_linear=False`), the inbound handler
        # sends its own SE + CSM notification, so firing again here would
        # double-DM (and `by_user_id` is then a non-Slack marker).
        if sync_to_linear:
            label = _RESOLUTION_LABEL[resolution_type]
            detail = f"Resolved via: {label}"
            if resolution_pr_link:
                detail += f" (<{resolution_pr_link}|PR>)"
            await notify_csms_status_change(
                self._slack,
                self._tickets,
                self._orgs,
                refreshed or ticket,
                status_label="Resolved",
                by_user_id=by_user_id,
                detail=detail,
            )

        # Linear mirror: the SE-facing ticket is silently closed (Done) for
        # reporting. `sync_to_linear=False` when driven by an inbound Linear
        # event, so we never echo a write back to Linear.
        if sync_to_linear and self._linear is not None:
            await self._linear.mark_done_silently(ticket.id, state=LinearWorkflowState.DONE)

        return ResolveResult(ticket=refreshed)
