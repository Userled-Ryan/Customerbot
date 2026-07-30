"""Pure mappings between customerbot tickets and Linear issues (v1.5).

No I/O — just the vocabulary translation in both directions, kept here so the
gateway, sync service, and inbound handler share one source of truth and it is
trivially unit-testable.

The team's Linear workflow states are configured to mirror customerbot 1:1, so
the forward and reverse maps are unambiguous (see the plan's status tables).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    TicketStatus,
    TicketType,
)

# Display names for the per-type Linear label, so reports can filter by
# ticket type (Bug / Config / FAQ / Product change). Kept here alongside the
# other Linear vocabulary mappings.
_TYPE_LABEL_NAMES: dict[TicketType, str] = {
    TicketType.BUG: "Bug",
    TicketType.CONFIG: "Config",
    TicketType.FAQ: "FAQ",
    TicketType.FEATURE_REQUEST: "Product change",
    TicketType.CSM_HELP: "CSM Help Request",
}


def type_label_name(ticket_type: TicketType) -> str:
    return _TYPE_LABEL_NAMES.get(ticket_type, ticket_type.value.capitalize())


# A GitHub pull-request URL, e.g. https://github.com/acme/app/pull/42. Used to
# decide, when a dev marks an issue Done, whether the resolve was a code change
# (a PR is linked) or not.
_GITHUB_PR_RE = re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+/pull/\d+", re.IGNORECASE)


def first_github_pr_url(candidates: Iterable[str | None]) -> str | None:
    """First GitHub PR URL found across the given texts (attachment urls, the
    issue description, …), or None. Order of `candidates` is the preference."""
    for text in candidates:
        if not text:
            continue
        match = _GITHUB_PR_RE.search(text)
        if match:
            return match.group(0)
    return None


def ticket_to_linear_state(
    status: TicketStatus, lane: Lane | None, *, urgent: bool = False
) -> LinearWorkflowState:
    """Expected Linear state for a ticket's current status + lane.

    An urgent SE-lane ticket awaiting first action (still NEW) lands in the
    dedicated URGENT section instead of Triage, so the SE can focus on it. Once
    it moves to In progress / Resolved — or is handed to the dev lane — it
    follows the normal mapping and leaves Urgent (Urgent is an SE-lane concept:
    a dev-lane ticket is being worked, so it shows as In Progress).

    CLOSED maps to DONE here; a *dropped* ticket is closed to CANCELED at the
    call site explicitly (the status alone can't distinguish drop from
    auto-close), and the reconcile sweep treats DONE/CANCELED as
    interchangeable terminal states so it never "repairs" a Canceled drop.
    """
    if status == TicketStatus.NEW and urgent and lane != Lane.DEV_ACTION:
        return LinearWorkflowState.URGENT
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

    Only DONE/CANCELED/IN_PROGRESS carry a transition; TRIAGE, URGENT and
    AWAITING_CUSTOMER are no-ops inbound (we never need the dev side to push the
    ticket *back* to those — Urgent is set by us at intake, never by Linear).
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


def build_issue_title(ticket: Ticket, org_names: list[str]) -> str:
    """Build the Linear issue title as `{orgs} · Bosh-NNN · {title}`.

    The company/org prefix lets tickets group visually by customer in Linear
    (clearer than the org label alone). The `Bosh-NNN` id keeps the originating
    ticket identifiable at a glance — Linear's own identifier (e.g. USR-123) is
    team-assigned and can't carry our id. Both the prefix and the id are dropped
    gracefully when absent.
    """
    parts: list[str] = []
    if org_names:
        parts.append(", ".join(org_names))
    if ticket.id is not None:
        parts.append(f"Bosh-{ticket.id:03d}")
    parts.append(ticket.title)
    return " · ".join(parts)[:250]


# Slack auto-links bare URLs into its mrkdwn form `<https://url|display>` (or
# `<https://url>` when no distinct label). Linear renders standard Markdown, so
# passing that verbatim strips the angle brackets but leaves `url|display` — the
# URL appears duplicated. Convert links to Markdown and unescape Slack's HTML
# entities before the text lands in a Linear issue body.
_SLACK_LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")


def _unescape_slack_entities(text: str) -> str:
    # Order matters: `&amp;` last so a real `&amp;` in the source doesn't get
    # turned into `&lt;`/`&gt;` sequences it never contained.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _slack_to_markdown(text: str) -> str:
    """Normalize Slack mrkdwn URL auto-links into standard Markdown."""

    def _replace(match: re.Match[str]) -> str:
        url, label = match.group(1), match.group(2)
        return f"[{label}]({url})" if label else url

    # Unescape once, after link rewriting, so entities inside URLs/labels are
    # handled too without risking a double pass.
    return _unescape_slack_entities(_SLACK_LINK_RE.sub(_replace, text))


def build_issue_description(
    ticket: Ticket, org_names: list[str], slack_link: str | None = None
) -> str:
    """Markdown body mirroring the content of the Slack handoff payload.

    `slack_link` is a permalink to the ticket's Slack card (where the *Resolved*
    button lives), surfaced up top so a dev/SE can jump back and close it in
    Slack directly.
    """
    orgs_text = ", ".join(org_names) if org_names else "—"
    lines: list[str] = [
        f"**customerbot** {ticket.display_id} · {ticket.type.value}/{ticket.subtype.value}",
    ]
    if slack_link:
        lines.append(f"👉 **[Manage in Slack]({slack_link})** — resolve or reopen from the card.")
    lines += [
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

    repro = (
        _slack_to_markdown(ticket.description).strip() or "_no repro steps captured — see thread_"
    )
    lines += ["", "**Repro / context:**", repro[:3000]]

    links: list[str] = []
    if ticket.original_slack_link:
        links.append(f"[Original thread]({ticket.original_slack_link})")
    if ticket.prod_link:
        links.append(f"[Prod link]({ticket.prod_link})")
    if ticket.campaign_url:
        links.append(f"[Campaign]({ticket.campaign_url})")
    if ticket.replay_link:
        links.append(f"[Link]({ticket.replay_link})")
    if ticket.screenshot_url:
        links.append(f"[Screenshot]({ticket.screenshot_url})")
    if links:
        lines += ["", " · ".join(links)]

    return "\n".join(lines)
