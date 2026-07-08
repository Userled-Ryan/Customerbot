"""Resolve-ticket modal (plan Part 2b — the `Resolved` ticket-card button).

Marking a ticket Resolved is terminal: the card retires and all reminders
stop. Before that happens we capture *how* it was resolved for reporting —
`No code change` or `Code change` (+ optional PR link) — so the resolve click
opens this modal rather than transitioning immediately.
"""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.value_objects import ResolutionType

CALLBACK_ID = "resolve_ticket"

BLOCK_RESOLUTION = "resolution"
BLOCK_PR_LINK = "pr_link"

ACTION_RESOLUTION = "resolution_radio"
ACTION_PR_LINK = "pr_link_input"


_RESOLUTION_OPTIONS: list[tuple[ResolutionType, str]] = [
    (ResolutionType.NO_CODE_CHANGE, "No code change"),
    (ResolutionType.CODE_CHANGE, "Code change"),
]


def build_view(*, ticket_id: int) -> dict[str, Any]:
    """Render the resolve modal. `private_metadata` carries the ticket id."""
    resolution_options = [
        {"text": {"type": "plain_text", "text": label}, "value": rt.value}
        for rt, label in _RESOLUTION_OPTIONS
    ]
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": str(ticket_id),
        "title": {"type": "plain_text", "text": "Resolve ticket"},
        "submit": {"type": "plain_text", "text": "Resolve"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Resolving is final — the card retires, all reminders stop, "
                        "and the customer's CSM is notified. You can still *Reopen* it "
                        "later if needed."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_RESOLUTION,
                "label": {"type": "plain_text", "text": "Resolved via"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": ACTION_RESOLUTION,
                    "options": resolution_options,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_PR_LINK,
                "label": {"type": "plain_text", "text": "PR link"},
                "element": {"type": "url_text_input", "action_id": ACTION_PR_LINK},
                "optional": True,
                "hint": {
                    "type": "plain_text",
                    "text": "Add if there's a PR — some code changes (DB, config) won't have one.",
                },
            },
        ],
    }
