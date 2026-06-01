"""Add-affected-org button + modal flow (plan Chunk 9).

Two-step interaction:

1. SE clicks `Add affected org` on a ticket card → `OpenAddOrgModal` opens
   the org-picker modal, carrying the ticket id in `private_metadata`.
2. SE picks an org → `SubmitAddAffectedOrg` adds the org to `ticket_orgs`,
   refreshes the card, and triggers the multi-customer prio-bump check
   (which DMs SE a bump suggestion if the new org count crosses §5c
   thresholds).

The Slack-level dependency on `views.open` is injected as a callable to keep
this module free of the integration layer; the integration layer's modal
view-builder is passed in by `main.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)


ViewBuilder = Callable[..., dict[str, Any]]


class _BumpCheckPort(Protocol):
    """Minimum surface SubmitAddAffectedOrg needs from MultiCustomerBumpCheck."""

    async def execute(self, ticket_id: int) -> object: ...


class OpenAddOrgModal:
    """Open the org-picker modal in response to the `Add affected org` click."""

    def __init__(
        self,
        slack: SlackPort,
        orgs: OrgRepositoryPort,
        tickets: TicketRepositoryPort,
        view_builder: ViewBuilder,
    ) -> None:
        self._slack = slack
        self._orgs = orgs
        self._tickets = tickets
        self._view_builder = view_builder

    async def execute(self, *, trigger_id: str, ticket_id: int) -> str | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None:
            logger.warning("Add affected org clicked on missing ticket %s", ticket_id)
            return None
        all_orgs = await self._orgs.list_all()
        existing = set(await self._tickets.list_orgs(ticket_id))
        view = self._view_builder(
            all_orgs,
            private_metadata=str(ticket_id),
            excluded_org_ids=existing,
        )
        return await self._slack.open_view(trigger_id, view)


class SubmitAddAffectedOrg:
    """Handle the `add_affected_org` modal `view_submission`."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
        bump_check: _BumpCheckPort | None,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._orgs = orgs
        self._bump_check = bump_check

    async def execute(self, *, ticket_id: int, org_id: str, by_user_id: str) -> Org | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Add affected org submitted for missing ticket %s", ticket_id)
            return None
        org = await self._orgs.get(org_id)
        if org is None:
            logger.warning(
                "Add affected org submitted with unknown org_id=%s (ticket %s)",
                org_id,
                ticket.display_id,
            )
            return None
        existing = await self._tickets.list_orgs(ticket.id)
        if org_id in existing:
            logger.info(
                "Add affected org no-op: %s already linked to %s",
                org_id,
                ticket.display_id,
            )
            return org
        await self._tickets.add_org(ticket.id, org_id)
        _ = by_user_id  # carried for future audit; ticket_orgs has no by-user col yet
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
        if self._bump_check is not None:
            await self._bump_check.execute(ticket.id)
        return org
