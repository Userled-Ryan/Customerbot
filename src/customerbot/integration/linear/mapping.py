"""Pure mappings between customerbot tickets and Linear issues (v1.5).

No I/O — just the vocabulary translation in both directions, kept here so the
gateway, sync service, and inbound handler share one source of truth and it is
trivially unit-testable.

The team's Linear workflow states are configured to mirror customerbot 1:1, so
the forward and reverse maps are unambiguous (see the plan's status tables).
"""

from __future__ import annotations

from enum import StrEnum

from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    TicketStatus,
)


def ticket_to_linear_state(status: TicketStatus, lane: Lane | None) -> LinearWorkflowState:
    """Expected Linear state for a ticket's current status + lane.

    CLOSED maps to DONE here; a *dropped* ticket is closed to CANCELED at the
    call site explicitly (the status alone can't distinguish drop from
    auto-close), and the reconcile sweep treats DONE/CANCELED as
    interchangeable terminal states so it never "repairs" a Canceled drop.
    """
    if status == TicketStatus.NEW:
        # A dev-lane ticket is being actively worked, so it's never just Triage.
        if lane == Lane.DEV_ACTION:
            return LinearWorkflowState.IN_PROGRESS
        return LinearWorkflowState.TRIAGE
    if status == TicketStatus.IN_PROGRESS:
        return LinearWorkflowState.IN_PROGRESS
    if status == TicketStatus.AWAITING_CUSTOMER:
        return LinearWorkflowState.AWAITING_CUSTOMER
    if status == TicketStatus.RESOLVED:
        return LinearWorkflowState.DONE
    # CLOSED
    return LinearWorkflowState.DONE


class InboundIntent(StrEnum):
    """What an inbound Linear state change means for the customerbot ticket."""

    REOPEN_IN_PROGRESS = "reopen_in_progress"  # dev started / re-opened work
    RESOLVE = "resolve"  # dev marked Done → returns to SE (Awaiting customer)
    DROP = "drop"  # dev canceled → Closed
    NONE = "none"  # no status-affecting change (Triage/Awaiting → ignore)


# Linear's built-in state *type* (carried on inbound webhooks) → our logical
# state. Robust against custom state names since the type is a fixed enum.
_STATE_TYPE_TO_WORKFLOW: dict[str, LinearWorkflowState] = {
    "completed": LinearWorkflowState.DONE,
    "canceled": LinearWorkflowState.CANCELED,
    "cancelled": LinearWorkflowState.CANCELED,
    "started": LinearWorkflowState.IN_PROGRESS,
    "triage": LinearWorkflowState.TRIAGE,
    "backlog": LinearWorkflowState.TRIAGE,
    "unstarted": LinearWorkflowState.TRIAGE,
}


def linear_state_type_to_workflow_state(state_type: str | None) -> LinearWorkflowState | None:
    if not state_type:
        return None
    return _STATE_TYPE_TO_WORKFLOW.get(state_type.lower())


def linear_state_to_inbound_intent(state: LinearWorkflowState) -> InboundIntent:
    """Reverse map: a dev's Linear state change → a customerbot transition.

    Only DONE/CANCELED/IN_PROGRESS carry a transition; TRIAGE and
    AWAITING_CUSTOMER are no-ops inbound (we never need the dev side to push the
    ticket *back* to those).
    """
    if state == LinearWorkflowState.DONE:
        return InboundIntent.RESOLVE
    if state == LinearWorkflowState.CANCELED:
        return InboundIntent.DROP
    if state == LinearWorkflowState.IN_PROGRESS:
        return InboundIntent.REOPEN_IN_PROGRESS
    return InboundIntent.NONE


# Linear priority scale: 0 none, 1 urgent, 2 high, 3 normal, 4 low.
# Five customerbot tiers collapse onto four Linear levels; this is a display
# hint in Linear, not load-bearing for any logic.
_PRIORITY_TO_LINEAR: dict[Priority, int] = {
    Priority.P0: 1,
    Priority.P1: 2,
    Priority.P2: 3,
    Priority.P3: 3,
    Priority.P4: 4,
}


def ticket_priority_to_linear(priority: Priority) -> int:
    return _PRIORITY_TO_LINEAR.get(priority, 3)


def build_issue_title(ticket: Ticket) -> str:
    """Prefix the Linear issue title with the customerbot ticket id (`Bosh-NNN`)
    so the originating ticket is identifiable at a glance — Linear's own
    identifier (e.g. USR-123) is team-assigned and can't carry our id."""
    if ticket.id is not None:
        return f"Bosh-{ticket.id:03d} · {ticket.title}"[:250]
    return ticket.title[:250]


def build_issue_description(ticket: Ticket, org_names: list[str]) -> str:
    """Markdown body mirroring the content of the Slack handoff payload."""
    orgs_text = ", ".join(org_names) if org_names else "—"
    lines: list[str] = [
        f"**customerbot** {ticket.display_id} · {ticket.type.value}/{ticket.subtype.value}",
        "",
        f"- **Priority:** {ticket.priority.value}",
        f"- **Severity:** {ticket.severity.value}",
        f"- **Affected orgs:** {orgs_text}",
        f"- **Source:** {ticket.source.value}",
    ]
    if ticket.affected_user:
        lines.append(f"- **Affected user:** {ticket.affected_user}")
    if ticket.deadline:
        lines.append(f"- **Deadline:** {ticket.deadline.isoformat()}")

    repro = ticket.description.strip() or "_no repro steps captured — see thread_"
    lines += ["", "**Repro / context:**", repro[:3000]]

    links: list[str] = []
    if ticket.original_slack_link:
        links.append(f"[Original thread]({ticket.original_slack_link})")
    if ticket.prod_link:
        links.append(f"[Prod link]({ticket.prod_link})")
    if ticket.replay_link:
        links.append(f"[Session replay]({ticket.replay_link})")
    if ticket.screenshot_url:
        links.append(f"[Screenshot]({ticket.screenshot_url})")
    if links:
        lines += ["", " · ".join(links)]

    return "\n".join(lines)
