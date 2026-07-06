"""Link-to-existing-ticket modal (the `Link to existing ticket` shortcut).

Picks the live ticket a #userled-support thread belongs to. Each option shows
`TIC-NNN · title · orgs` so the SE can recognise the right one at a glance. When
the thread is already attached elsewhere, a warning block flags that submitting
will move it — the submit is the confirmation.
"""

from __future__ import annotations

from typing import Any

CALLBACK_ID = "link_ticket"

BLOCK_TICKET = "ticket"
ACTION_TICKET = "ticket_select"


def build_view(
    ticket_options: list[tuple[int, str]],
    *,
    private_metadata: str,
    current_note: str | None = None,
) -> dict[str, Any]:
    """Return the modal view JSON.

    `ticket_options` is `(ticket_id, label)` pairs; `private_metadata` carries
    `channel_id|thread_ts`.
    """
    if not ticket_options:
        return _no_tickets_view(private_metadata=private_metadata)

    options = [
        {
            "text": {"type": "plain_text", "text": label[:75]},
            "value": str(ticket_id),
        }
        for ticket_id, label in ticket_options[:100]
    ]

    blocks: list[dict[str, Any]] = []
    if current_note:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": current_note}})
    blocks.append(
        {
            "type": "input",
            "block_id": BLOCK_TICKET,
            "label": {"type": "plain_text", "text": "Ticket"},
            "element": {
                "type": "static_select",
                "action_id": ACTION_TICKET,
                "placeholder": {"type": "plain_text", "text": "Pick a live ticket"},
                "options": options,
            },
        }
    )

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Link to ticket"},
        "submit": {"type": "plain_text", "text": "Link"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def _no_tickets_view(*, private_metadata: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Link to ticket"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":information_source: There are no live tickets to link to. "
                        "Log the ticket first, then link additional threads to it."
                    ),
                },
            }
        ],
    }
