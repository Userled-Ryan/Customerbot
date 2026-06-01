"""Set-deadline modal — capture an unblock-by date on a ticket.

Single datepicker. Marked optional so SE can clear a previously-set
deadline by submitting with no date selected. `private_metadata` carries
the ticket id so the submission handler can route without state lookups.
"""

from __future__ import annotations

from datetime import date
from typing import Any

CALLBACK_ID = "set_deadline"

BLOCK_DEADLINE = "deadline"
ACTION_DEADLINE = "deadline_pick"


def build_view(*, ticket_id: int, current_deadline: date | None) -> dict[str, Any]:
    deadline_element: dict[str, Any] = {
        "type": "datepicker",
        "action_id": ACTION_DEADLINE,
        "placeholder": {"type": "plain_text", "text": "Pick a date"},
    }
    if current_deadline is not None:
        deadline_element["initial_date"] = current_deadline.isoformat()
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": str(ticket_id),
        "title": {"type": "plain_text", "text": "Set deadline"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": ("_Leave blank and Save to clear an existing deadline._"),
                    }
                ],
            },
            {
                "type": "input",
                "block_id": BLOCK_DEADLINE,
                "label": {"type": "plain_text", "text": "Unblock by"},
                "element": deadline_element,
                "optional": True,
            },
        ],
    }
