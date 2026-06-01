"""Ticket-card Slack message builder.

The v1 replacement for the Notion board (decision #5). Each ticket gets one
Slack message in the configured `SE_TICKETS_CHANNEL_ID` channel; the bot
`chat.update`s the same message on every state change so the card is always
the live view.

This module is pure block-rendering — no I/O. Button handlers land in Chunk 9.
"""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import Lane, Priority, TicketStatus

ACTION_MOVE_TO_DEV = "ticket_move_to_dev"
ACTION_RESOLVED = "ticket_resolved"
ACTION_RESOLVED_HOTFIX = "ticket_resolved_hotfix"
ACTION_RECLASSIFY = "ticket_reclassify"
ACTION_REOPEN = "ticket_reopen"
ACTION_ADD_AFFECTED_ORG = "ticket_add_affected_org"


_STATUS_LABEL: dict[TicketStatus, str] = {
    TicketStatus.NEW: "New",
    TicketStatus.IN_PROGRESS: "In progress",
    TicketStatus.AWAITING_CUSTOMER: "Awaiting customer",
    TicketStatus.RESOLVED: "Resolved",
    TicketStatus.CLOSED: "Closed",
}

_LANE_LABEL: dict[Lane, str] = {
    Lane.SE_ACTION: "SE Action",
    Lane.DEV_ACTION: "Dev Action",
}

_PRIORITY_EMOJI: dict[Priority, str] = {
    Priority.P0: ":rotating_light:",
    Priority.P1: ":red_circle:",
    Priority.P2: ":large_orange_circle:",
    Priority.P3: ":large_yellow_circle:",
    Priority.P4: ":white_circle:",
}


def build_blocks(ticket: Ticket, affected_org_names: list[str]) -> list[dict[str, Any]]:
    """Return the Block-Kit blocks for the ticket card.

    Buttons are always rendered; their handlers no-op until Chunk 9. The button
    `value` carries the ticket id so handlers can route without state lookups.
    """
    prio_emoji = _PRIORITY_EMOJI[ticket.priority]
    status_label = _STATUS_LABEL[ticket.status]
    lane_label = _LANE_LABEL[ticket.lane] if ticket.lane else "—"
    orgs_text = ", ".join(affected_org_names) if affected_org_names else "_no orgs linked_"

    header_text = f"*{ticket.display_id} · {ticket.title}*"
    metadata_text = (
        f"{prio_emoji} *{ticket.priority.value}* · "
        f":label: {ticket.type.value} / {ticket.subtype.value} · "
        f"*{status_label}*"
    )
    if ticket.lane is not None:
        metadata_text += f" · :traffic_light: {lane_label}"

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": metadata_text}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Severity*\n{ticket.severity.value}"},
                {"type": "mrkdwn", "text": f"*Source*\n{ticket.source.value}"},
                {"type": "mrkdwn", "text": f"*Reporter*\n<@{ticket.reporter_user_id}>"},
                {"type": "mrkdwn", "text": f"*Affected orgs*\n{orgs_text}"},
            ],
        },
    ]

    if ticket.description:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate_for_section(ticket.description),
                },
            }
        )

    if ticket.original_slack_link:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f":link: <{ticket.original_slack_link}|Original thread>",
                    }
                ],
            }
        )

    value = str(ticket.id) if ticket.id is not None else ""
    blocks.append(
        {
            "type": "actions",
            "elements": [
                _button("Resolved", ACTION_RESOLVED, value),
                _button("Resolved via hotfix", ACTION_RESOLVED_HOTFIX, value),
                _button("Move to Dev Action", ACTION_MOVE_TO_DEV, value),
                _button("Reclassify", ACTION_RECLASSIFY, value),
                _button("Add affected org", ACTION_ADD_AFFECTED_ORG, value),
                _button("Reopen", ACTION_REOPEN, value),
            ],
        }
    )

    return blocks


def _button(label: str, action_id: str, value: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "value": value,
    }


def _truncate_for_section(text: str, limit: int = 2900) -> str:
    """Slack section text max is 3000 chars; leave a small margin."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def fallback_text(ticket: Ticket) -> str:
    """Plain-text fallback for the message (notifications, screenreaders)."""
    return f"{ticket.display_id} {ticket.title} ({ticket.priority.value} · {ticket.status.value})"
