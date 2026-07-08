"""On-demand ticket board (plan Chunk 13).

`/board` renders a snapshot of live tickets grouped by lane × status,
returned as Block-Kit blocks the handler posts ephemerally. Without
Notion (decision #5), this is the "filter view" surface — SE typing
`/board` is the cheapest way to ask "what's open right now?".

Pure rendering: takes already-loaded tickets, no I/O. The handler is
responsible for hitting the repository.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from customerbot.application.tracking.links import linked_display_id, linked_text
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import (
    LIVE_STATUSES,
    Lane,
    Priority,
    TicketStatus,
)

_STATUS_ORDER: tuple[TicketStatus, ...] = (
    TicketStatus.NEW,
    TicketStatus.IN_PROGRESS,
    TicketStatus.AWAITING_CUSTOMER,
    TicketStatus.RESOLVED,
)

_LANE_ORDER: tuple[Lane | None, ...] = (Lane.SE_ACTION, Lane.DEV_ACTION, None)

_LANE_LABEL: dict[Lane | None, str] = {
    Lane.SE_ACTION: ":hammer_and_wrench: SE Action",
    Lane.DEV_ACTION: ":hammer: Dev Action",
    None: ":grey_question: No lane",
}

_STATUS_LABEL: dict[TicketStatus, str] = {
    TicketStatus.NEW: "New",
    TicketStatus.IN_PROGRESS: "In progress",
    TicketStatus.AWAITING_CUSTOMER: "Awaiting customer",
    TicketStatus.RESOLVED: "Resolved",
}

_PRIO_EMOJI: dict[Priority, str] = {
    Priority.P0: ":rotating_light:",
    Priority.P1: ":red_circle:",
    Priority.P2: ":large_orange_circle:",
    Priority.P3: ":large_yellow_circle:",
    Priority.P4: ":white_circle:",
}


class RenderTicketsBoard:
    """Build the `/board` snapshot grouped by lane × status."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
        workspace_url: str,
    ) -> None:
        self._tickets = tickets
        self._orgs = orgs
        self._workspace_url = workspace_url

    async def execute(self) -> list[dict[str, Any]]:
        live = await self._tickets.query_live()
        # query_live() already filters to live statuses, but be defensive against
        # any future repo change that loosens that contract.
        live = [t for t in live if t.status in LIVE_STATUSES]
        if not live:
            return [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": ":clipboard: *Board* — _no live tickets._"},
                }
            ]

        # Pre-render the org-name lookup for each ticket so we don't hit the
        # repo inside the rendering loop.
        org_names_by_ticket = await self._org_names_by_ticket(live)

        # Group by lane → status → list[ticket].
        grouped: dict[Lane | None, dict[TicketStatus, list[Ticket]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for t in live:
            grouped[t.lane][t.status].append(t)

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":clipboard: *Board* — *{len(live)}* live ticket(s) "
                        f"across {len(grouped)} lane(s)."
                    ),
                },
            },
            {"type": "divider"},
        ]
        for lane in _LANE_ORDER:
            by_status = grouped.get(lane)
            if not by_status:
                continue
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{_LANE_LABEL[lane]}*"},
                }
            )
            for status in _STATUS_ORDER:
                tickets = by_status.get(status)
                if not tickets:
                    continue
                # Sort by priority then by id (P0 first, oldest within tier first).
                tickets_sorted = sorted(
                    tickets, key=lambda t: (_priority_rank(t.priority), t.id or 0)
                )
                today = datetime.now(UTC).date()
                lines = [
                    _render_ticket_line(
                        t,
                        org_names=org_names_by_ticket.get(t.id or 0, []),
                        workspace_url=self._workspace_url,
                        today=today,
                    )
                    for t in tickets_sorted
                ]
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"_{_STATUS_LABEL[status]}_ ({len(tickets)})\n"
                            + "\n".join(lines),
                        },
                    }
                )
            blocks.append({"type": "divider"})
        return blocks

    async def _org_names_by_ticket(self, tickets: list[Ticket]) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for t in tickets:
            if t.id is None:
                continue
            org_ids = await self._tickets.list_orgs(t.id)
            names: list[str] = []
            for org_id in org_ids:
                org = await self._orgs.get(org_id)
                names.append(org.name if org else org_id)
            out[t.id] = names
        return out


def _render_ticket_line(
    ticket: Ticket, *, org_names: list[str], workspace_url: str, today: date
) -> str:
    emoji = _PRIO_EMOJI[ticket.priority]
    title = _truncate(ticket.title, 60)
    # Link the company name(s) to the original customer thread so SE can jump
    # straight to the context the ticket was raised from (the `TIC-NNN` link
    # already points at the ticket card).
    orgs_text = linked_text(", ".join(org_names), ticket) if org_names else "_no orgs_"
    deadline_text = _deadline_segment(ticket.deadline, today)
    return (
        f"• {emoji} *{linked_display_id(ticket, workspace_url)}* {title} "
        f"({ticket.priority.value} · {ticket.type.value}/{ticket.subtype.value}) "
        f"— {orgs_text}{deadline_text}"
    )


def _deadline_segment(deadline: date | None, today: date) -> str:
    """Render the deadline as days-remaining plus the date, or "" if none.

    Overdue deadlines are flagged so an SE can spot them at a glance; a
    deadline landing today reads "due today".
    """
    if deadline is None:
        return ""
    days = (deadline - today).days
    pretty = deadline.strftime("%d %b")
    if days < 0:
        overdue = -days
        label = f":rotating_light: overdue {overdue}d ({pretty})"
    elif days == 0:
        label = f":alarm_clock: due today ({pretty})"
    else:
        label = f":calendar: due in {days}d ({pretty})"
    return f" · {label}"


def _priority_rank(priority: Priority) -> int:
    order = (Priority.P0, Priority.P1, Priority.P2, Priority.P3, Priority.P4)
    return order.index(priority)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
