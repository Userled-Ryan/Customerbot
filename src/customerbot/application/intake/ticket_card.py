"""Ticket-card Slack message builder.

The v1 replacement for the Notion board (decision #5). Each ticket gets one
Slack message in the configured `SE_TICKETS_CHANNEL_ID` channel; the bot
`chat.update`s the same message on every state change so the card is always
the live view.

Block-rendering is pure (no I/O). `refresh_card` ties everything together
for the Chunk-9 lifecycle handlers that mutate ticket state and need the
card to reflect the change.
"""

from __future__ import annotations

import logging
from typing import Any

from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import Lane, Priority, TicketStatus, TicketType

logger = logging.getLogger(__name__)

ACTION_MOVE_TO_DEV = "ticket_move_to_dev"
ACTION_RESOLVED = "ticket_resolved"
ACTION_RESOLVED_HOTFIX = "ticket_resolved_hotfix"
ACTION_RECLASSIFY = "ticket_reclassify"
ACTION_REOPEN = "ticket_reopen"
ACTION_DROP = "ticket_drop"
ACTION_ADD_AFFECTED_ORG = "ticket_add_affected_org"
ACTION_NEEDS_ARTICLE = "ticket_needs_article"
ACTION_SET_DEADLINE = "ticket_set_deadline"


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

# Header prefix per status so the card reads its lifecycle stage at a glance.
# New / In progress carry no prefix (the default working state); the
# "wrapping up" states get a check, and a dropped/closed ticket gets a lock
# so it's unmistakable from the live ones in the channel.
_STATUS_HEADER_EMOJI: dict[TicketStatus, str] = {
    TicketStatus.AWAITING_CUSTOMER: ":white_check_mark: ",
    TicketStatus.RESOLVED: ":white_check_mark: ",
    TicketStatus.CLOSED: ":lock: ",
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

    header_prefix = _STATUS_HEADER_EMOJI.get(ticket.status, "")
    # A dropped/closed ticket reads as struck-through so it's visually retired.
    title_text = f"~{ticket.title}~" if ticket.status == TicketStatus.CLOSED else ticket.title
    header_text = f"{header_prefix}*{ticket.display_id} · {title_text}*"
    metadata_text = (
        f"{prio_emoji} *{ticket.priority.value}* · "
        f":label: {ticket.type.value} / {ticket.subtype.value} · "
        f"*{status_label}*"
    )
    if ticket.lane is not None:
        metadata_text += f" · :traffic_light: {lane_label}"

    deadline_text = ticket.deadline.strftime("%a %d %b %Y") if ticket.deadline else "—"
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
                {"type": "mrkdwn", "text": f"*Deadline*\n{deadline_text}"},
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
    # A closed/dropped ticket is retired — the only sensible action is to
    # bring it back if more context appears, so collapse the card to a single
    # Reopen button. Reopen on a live ticket no-ops, so it's deliberately
    # absent from the live button set.
    if ticket.status == TicketStatus.CLOSED:
        blocks.append({"type": "actions", "elements": [_button("Reopen", ACTION_REOPEN, value)]})
        return blocks

    blocks.append(
        {
            "type": "actions",
            "elements": [
                _button("Resolved", ACTION_RESOLVED, value),
                _button("Resolved via hotfix", ACTION_RESOLVED_HOTFIX, value),
                _button("Move to Dev Action", ACTION_MOVE_TO_DEV, value),
                _button("Reclassify", ACTION_RECLASSIFY, value),
                _button("Add affected org", ACTION_ADD_AFFECTED_ORG, value),
                _drop_button(value),
            ],
        }
    )
    # Secondary actions row: deadline always, plus the FAQ-only "Needs
    # article" when applicable. Kept separate from the primary six-button
    # row so Slack doesn't wrap them into a less-readable layout.
    secondary_elements: list[dict[str, Any]] = [
        _button(
            "Set deadline" if ticket.deadline is None else "Change deadline",
            ACTION_SET_DEADLINE,
            value,
        ),
    ]
    if ticket.type == TicketType.FAQ:
        secondary_elements.append(_button("Needs article", ACTION_NEEDS_ARTICLE, value))
    blocks.append({"type": "actions", "elements": secondary_elements})

    return blocks


def _button(label: str, action_id: str, value: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "value": value,
    }


def _drop_button(value: str) -> dict[str, Any]:
    """`Drop` closes the ticket. It's destructive (stops every reminder and
    retires the card), so it carries a native Slack confirmation dialog —
    nothing happens until the SE confirms."""
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": "Drop"},
        "action_id": ACTION_DROP,
        "value": value,
        "style": "danger",
        "confirm": {
            "title": {"type": "plain_text", "text": "Drop this ticket?"},
            "text": {
                "type": "mrkdwn",
                "text": (
                    "This closes the ticket and stops all reminders. "
                    "You can *Reopen* it within 30 days if more context appears."
                ),
            },
            "confirm": {"type": "plain_text", "text": "Drop"},
            "deny": {"type": "plain_text", "text": "Cancel"},
        },
    }


def _truncate_for_section(text: str, limit: int = 2900) -> str:
    """Slack section text max is 3000 chars; leave a small margin."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def fallback_text(ticket: Ticket) -> str:
    """Plain-text fallback for the message (notifications, screenreaders)."""
    return f"{ticket.display_id} {ticket.title} ({ticket.priority.value} · {ticket.status.value})"


async def refresh_card(
    slack: SlackPort,
    tickets: TicketRepositoryPort,
    orgs: OrgRepositoryPort,
    ticket_id: int,
) -> None:
    """Re-render the ticket card from current state and `chat.update` it.

    No-op if the ticket has no card (e.g. SE_TICKETS_CHANNEL_ID wasn't set
    when the ticket was created). Safe to call after any state change.
    """
    ticket = await tickets.get(ticket_id)
    if ticket is None or not ticket.card_channel_id or not ticket.card_message_ts:
        return
    org_ids = await tickets.list_orgs(ticket_id)
    org_names: list[str] = []
    for org_id in org_ids:
        org = await orgs.get(org_id)
        org_names.append(org.name if org else org_id)
    blocks = build_blocks(ticket, org_names)
    await slack.update_message(
        ticket.card_channel_id,
        ticket.card_message_ts,
        blocks,
        text=fallback_text(ticket),
    )
