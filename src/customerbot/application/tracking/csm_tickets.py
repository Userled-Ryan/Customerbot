"""CSM-scoped ticket views — the shared core behind the Friday digest and
the on-demand `/mytickets` command.

A CSM "owns" a ticket transitively: a ticket is linked to one or more affected
orgs (`ticket_orgs`), and each org carries a `csm_user_id`. A CSM therefore
sees every live ticket touching one of *their* orgs — regardless of who raised
it (SE or CSM) or its type (bug / config / FAQ). That's the whole point: the
CSM gets one view of everything in flight for their customers.

`CSMTicketsView.tickets_by_csm()` builds the full `csm -> tickets` grouping in
one pass (used by the scheduled Friday digest); `tickets_for_csm()` is the
single-CSM slice (used by `/mytickets`). Both return each ticket paired with
the names of *that CSM's* affected orgs, so the reader sees which of their
customers is impacted. Rendering is a pure function so tests can assert on it
without any I/O.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from customerbot.application.tracking.links import linked_display_id
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import Priority

# (ticket, [affected-org names for this CSM]) — the unit both surfaces render.
CSMTicket = tuple[Ticket, list[str]]

_PRIO_ORDER: tuple[Priority, ...] = (
    Priority.P0,
    Priority.P1,
    Priority.P2,
    Priority.P3,
    Priority.P4,
)

_PRIO_EMOJI: dict[Priority, str] = {
    Priority.P0: ":rotating_light:",
    Priority.P1: ":red_circle:",
    Priority.P2: ":large_orange_circle:",
    Priority.P3: ":large_yellow_circle:",
    Priority.P4: ":white_circle:",
}


class CSMTicketsView:
    """Group live tickets by the CSM who owns the affected org(s)."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
        workspace_url: str,
    ) -> None:
        self._tickets = tickets
        self._orgs = orgs
        self._workspace_url = workspace_url

    @property
    def workspace_url(self) -> str:
        return self._workspace_url

    async def tickets_by_csm(self) -> dict[str, list[CSMTicket]]:
        """`csm_user_id -> [(ticket, [org name, ...]), ...]` over all live tickets.

        Each ticket appears at most once per CSM even if it touches two of that
        CSM's orgs (the org names are merged into the one entry).
        """
        orgs = await self._orgs.list_all()
        csm_by_org = {o.id: o.csm_user_id for o in orgs if o.csm_user_id}
        name_by_org = {o.id: o.name for o in orgs}

        live = await self._tickets.query_live()
        grouped: dict[str, list[CSMTicket]] = defaultdict(list)
        for ticket in live:
            if ticket.id is None:
                continue
            # Collect, per owning CSM, the names of their orgs this ticket hits.
            names_by_csm: dict[str, list[str]] = defaultdict(list)
            for org_id in await self._tickets.list_orgs(ticket.id):
                csm = csm_by_org.get(org_id)
                if csm:
                    names_by_csm[csm].append(name_by_org.get(org_id, org_id))
            for csm, names in names_by_csm.items():
                grouped[csm].append((ticket, names))
        return grouped

    async def tickets_for_csm(self, csm_user_id: str) -> list[CSMTicket]:
        """The single-CSM slice — live tickets touching this CSM's orgs."""
        grouped = await self.tickets_by_csm()
        return grouped.get(csm_user_id, [])


def render_csm_tickets_blocks(
    items: list[CSMTicket],
    *,
    workspace_url: str,
    scheduled: bool,
) -> list[dict[str, Any]]:
    """Pure rendering of a CSM's tickets, grouped by priority tier.

    `scheduled` only tweaks the headline wording (Friday DM vs on-demand).
    """
    if not items:
        empty = (
            ":white_check_mark: *Your tickets* — nothing open for your customers right now."
            if not scheduled
            else ":white_check_mark: *Your tickets this week* — nothing open for your customers."
        )
        return [{"type": "section", "text": {"type": "mrkdwn", "text": empty}}]

    headline = (
        f":ticket: *Your tickets this week* — *{len(items)}* open for your customers."
        if scheduled
        else f":ticket: *Your tickets* — *{len(items)}* open for your customers."
    )

    by_tier: dict[Priority, list[CSMTicket]] = defaultdict(list)
    for ticket, names in items:
        by_tier[ticket.priority].append((ticket, names))

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "divider"},
    ]
    for tier in _PRIO_ORDER:
        tier_items = by_tier.get(tier)
        if not tier_items:
            continue
        # Oldest first within a tier.
        tier_items.sort(key=lambda it: it[0].id or 0)
        lines = [_render_line(t, names, workspace_url) for t, names in tier_items]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{_PRIO_EMOJI[tier]} *{tier.value}* ({len(tier_items)})\n"
                    + "\n".join(lines),
                },
            }
        )
    return blocks


def _render_line(ticket: Ticket, org_names: list[str], workspace_url: str) -> str:
    orgs_text = ", ".join(org_names) if org_names else "_your customer_"
    return (
        f"• {linked_display_id(ticket, workspace_url)} _{_truncate(ticket.title, 60)}_ "
        f"({ticket.type.value} · {ticket.status.value}) — {orgs_text}"
    )


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
