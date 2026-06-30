"""LinearSync — outbound mirror orchestration (v1.5, Chunk C).

The single place that owns: idempotency (create once, keyed on
`ticket.linear_issue_id`), the create-then-close-silently logic for tickets the
SE resolves directly, and — crucially — **failure isolation**. Every public
method is wrapped so it can never raise into its caller: a Linear outage leaves
the authoritative SQLite/Slack state untouched and is only logged. The
reconcile sweep (Chunk D) repairs any drift later.

Lifecycle handlers depend on this class (not the raw `LinearPort`) and call one
line after their own DB + Slack work has completed.
"""

from __future__ import annotations

import logging

from customerbot.domain.linear.ports import LinearPort, LinearWorkflowState
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import Lane
from customerbot.integration.linear.mapping import (
    build_issue_description,
    build_issue_title,
    ticket_priority_to_linear,
    ticket_to_linear_state,
)

logger = logging.getLogger(__name__)


class LinearSync:
    def __init__(
        self,
        *,
        linear: LinearPort,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
    ) -> None:
        self._linear = linear
        self._tickets = tickets
        self._orgs = orgs

    async def mirror_new_ticket(self, ticket: Ticket) -> None:
        """Create the Linear mirror for a freshly-created ticket (idempotent)."""
        try:
            if ticket.id is None or ticket.linear_issue_id is not None:
                return
            await self._create_and_persist(ticket)
        except Exception:
            logger.exception("Linear mirror_new_ticket failed for ticket %s", ticket.id)

    async def mark_done_silently(
        self, ticket_id: int, *, state: LinearWorkflowState = LinearWorkflowState.DONE
    ) -> None:
        """Ensure a mirror exists, then move it to a terminal state — no alert.

        Used when the SE resolves/drops directly in Slack: the issue still gets
        created (for CTO reporting) and is immediately closed.
        """
        try:
            issue_id = await self._ensure_issue(ticket_id)
            if issue_id is None:
                return
            await self._linear.update_issue_state(issue_id=issue_id, state=state)
        except Exception:
            logger.exception("Linear mark_done_silently failed for ticket %s", ticket_id)

    async def sync_state(self, ticket_id: int) -> None:
        """Push the ticket's current status/lane onto its mirror.

        Used for transitions that don't have a dedicated method (e.g. reopen),
        so a Slack-side state change can't leave Linear stale.
        """
        try:
            issue_id = await self._ensure_issue(ticket_id)
            if issue_id is None:
                return
            ticket = await self._tickets.get(ticket_id)
            if ticket is None:
                return
            await self._linear.update_issue_state(
                issue_id=issue_id,
                state=ticket_to_linear_state(ticket.status, ticket.lane),
            )
        except Exception:
            logger.exception("Linear sync_state failed for ticket %s", ticket_id)

    async def sync_priority(self, ticket_id: int) -> None:
        """Push the ticket's current priority onto its mirror.

        Called after an SE priority change (card select / override DM / bump /
        P0 confirm) so the Linear issue's priority never drifts from Slack.
        """
        try:
            issue_id = await self._ensure_issue(ticket_id)
            if issue_id is None:
                return
            ticket = await self._tickets.get(ticket_id)
            if ticket is None:
                return
            await self._linear.update_issue_priority(
                issue_id=issue_id,
                priority=ticket_priority_to_linear(ticket.priority),
            )
        except Exception:
            logger.exception("Linear sync_priority failed for ticket %s", ticket_id)

    async def ensure_open_for_dev(self, ticket_id: int) -> None:
        """Move the mirror into the open dev state and add it to the project."""
        try:
            issue_id = await self._ensure_issue(ticket_id)
            if issue_id is None:
                return
            await self._linear.update_issue_state(
                issue_id=issue_id, state=LinearWorkflowState.IN_PROGRESS
            )
            await self._linear.add_to_project(issue_id=issue_id)
        except Exception:
            logger.exception("Linear ensure_open_for_dev failed for ticket %s", ticket_id)

    # -- internals ----------------------------------------------------------

    async def _ensure_issue(self, ticket_id: int) -> str | None:
        """Return the ticket's Linear issue id, creating the mirror if missing."""
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            return None
        if ticket.linear_issue_id is not None:
            return ticket.linear_issue_id
        return await self._create_and_persist(ticket)

    async def _create_and_persist(self, ticket: Ticket) -> str | None:
        assert ticket.id is not None
        org_ids = await self._tickets.list_orgs(ticket.id)
        org_names: list[str] = []
        label_ids: list[str] = []
        for org_id in org_ids:
            org = await self._orgs.get(org_id)
            name = org.name if org is not None else org_id
            org_names.append(name)
            label_id = await self._linear.ensure_org_label(org_id=org_id, name=name)
            if label_id is not None:
                label_ids.append(label_id)

        ref = await self._linear.create_issue(
            title=build_issue_title(ticket),
            description=build_issue_description(ticket, org_names),
            state=ticket_to_linear_state(ticket.status, ticket.lane),
            priority=ticket_priority_to_linear(ticket.priority),
            label_ids=label_ids,
            in_project=ticket.lane == Lane.DEV_ACTION,
        )
        if ref is None:
            return None
        await self._tickets.set_linear_issue(
            ticket.id, issue_id=ref.issue_id, identifier=ref.identifier, url=ref.url
        )
        return ref.issue_id
