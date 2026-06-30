"""Set-stakeholder button + modal flow.

Two-step:

1. SE clicks `Set stakeholder` on a ticket card → `OpenSetStakeholderModal`
   opens a CSM picker per affected org, each pre-filled with the org's
   current CSM.
2. SE picks (or clears) CSMs → `SubmitSetStakeholder` writes the change to
   each org's `csm_user_id` and refreshes the card.

The change is made on the *org*, not the ticket, so it sticks: the card's
Stakeholders field is derived from each affected org's CSM, and every other
ticket touching that org picks up the new CSM too.

The Slack-level `views.open` dependency is injected as a callable so this
module stays free of the integration layer; `main.py` passes the integration
layer's view-builder in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)


# `view_builder(ticket_id, orgs) -> view JSON`.
ViewBuilder = Callable[..., dict[str, Any]]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OpenSetStakeholderModal:
    """Open the per-org CSM picker in response to the `Set stakeholder` click."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
        view_builder: ViewBuilder,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._orgs = orgs
        self._view_builder = view_builder

    async def execute(self, *, trigger_id: str, ticket_id: int) -> str | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None:
            logger.warning("Set stakeholder clicked on missing ticket %s", ticket_id)
            return None
        org_rows: list[tuple[str, str, str | None]] = []
        for org_id in await self._tickets.list_orgs(ticket_id):
            org = await self._orgs.get(org_id)
            if org is None:
                continue
            org_rows.append((org.id, org.name, org.csm_user_id))
        view = self._view_builder(ticket_id=ticket_id, orgs=org_rows)
        return await self._slack.open_view(trigger_id, view)


class SubmitSetStakeholder:
    """Handle the `set_stakeholder` modal `view_submission`.

    `assignments` maps `org_id -> new_csm_user_id_or_None`; only orgs present
    in the map are touched (a picker the SE never rendered/changed is left
    alone), and a `None` value clears the org's CSM.
    """

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._orgs = orgs

    async def execute(
        self, *, ticket_id: int, assignments: dict[str, str | None], by_user_id: str
    ) -> bool:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Set stakeholder submitted for missing ticket %s", ticket_id)
            return False
        _ = by_user_id  # carried for future audit; orgs has no by-user col yet
        changed = False
        for org_id in await self._tickets.list_orgs(ticket.id):
            if org_id not in assignments:
                continue
            new_csm = assignments[org_id]
            org = await self._orgs.get(org_id)
            if org is None or org.csm_user_id == new_csm:
                continue
            org.csm_user_id = new_csm
            org.updated_at = _utcnow()
            await self._orgs.upsert(org)
            changed = True
        if changed:
            await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
        return changed
