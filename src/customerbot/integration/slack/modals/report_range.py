"""Date-range modal for `/report`.

Two required date pickers (start / end), prefilled by the caller to Monday of
the current week → today. `private_metadata` carries the invoking channel + user
(JSON) so the submission handler knows where to post the ephemeral report.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

CALLBACK_ID = "report_range"

BLOCK_START = "report_start"
BLOCK_END = "report_end"
ACTION_START = "report_start_pick"
ACTION_END = "report_end_pick"


def build_view(*, channel_id: str, user_id: str, start: date, end: date) -> dict[str, Any]:
    metadata = json.dumps({"channel_id": channel_id, "user_id": user_id})
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "Product report"},
        "submit": {"type": "plain_text", "text": "Generate"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "_Summarises the product improvements resolved in this "
                            "window — safe to copy into a customer channel._"
                        ),
                    }
                ],
            },
            {
                "type": "input",
                "block_id": BLOCK_START,
                "label": {"type": "plain_text", "text": "From"},
                "element": {
                    "type": "datepicker",
                    "action_id": ACTION_START,
                    "initial_date": start.isoformat(),
                    "placeholder": {"type": "plain_text", "text": "Start date"},
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_END,
                "label": {"type": "plain_text", "text": "To"},
                "element": {
                    "type": "datepicker",
                    "action_id": ACTION_END,
                    "initial_date": end.isoformat(),
                    "placeholder": {"type": "plain_text", "text": "End date"},
                },
            },
        ],
    }
