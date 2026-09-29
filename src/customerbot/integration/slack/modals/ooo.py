"""Sick / holiday modal for `/ooo`.

Lists who's out right now, then: *who* (prefilled with the invoker), *status*
(Out / Back), an optional *back on* date, an optional *cover*, and a checkbox to
also move their already-open tickets. The date / cover / checkbox are ignored
when marking someone back. `private_metadata` carries the invoking channel +
user (JSON) so the submission handler can post an ephemeral confirmation.
"""

from __future__ import annotations

import json
from typing import Any

from customerbot.application.intake.availability import describe_absence
from customerbot.domain.tickets.entities import SeAbsence

CALLBACK_ID = "ooo"

BLOCK_WHO = "ooo_who"
BLOCK_STATUS = "ooo_status"
BLOCK_BACK_ON = "ooo_back_on"
BLOCK_COVER = "ooo_cover"
BLOCK_MOVE_OPEN = "ooo_move_open"
ACTION_WHO = "ooo_who_pick"
ACTION_STATUS = "ooo_status_pick"
ACTION_BACK_ON = "ooo_back_on_pick"
ACTION_COVER = "ooo_cover_pick"
ACTION_MOVE_OPEN = "ooo_move_open_toggle"

STATUS_OUT = "out"
STATUS_BACK = "back"
MOVE_OPEN_VALUE = "move_open"


def _option(text: str, value: str) -> dict[str, Any]:
    return {"text": {"type": "plain_text", "text": text}, "value": value}


def build_view(*, channel_id: str, user_id: str, absences: list[SeAbsence]) -> dict[str, Any]:
    metadata = json.dumps({"channel_id": channel_id, "user_id": user_id})
    current = (
        "\n".join(describe_absence(a) for a in absences)
        if absences
        else ":white_check_mark: Nobody is marked out."
    )
    out_option = _option("Out — reroute their new tickets", STATUS_OUT)
    move_option = _option("Also move their open tickets to the cover", MOVE_OPEN_VALUE)
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "Out of office"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": current}},
            {"type": "divider"},
            {
                "type": "input",
                "block_id": BLOCK_WHO,
                "label": {"type": "plain_text", "text": "Who"},
                "element": {
                    "type": "users_select",
                    "action_id": ACTION_WHO,
                    "initial_user": user_id,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_STATUS,
                "label": {"type": "plain_text", "text": "Status"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": ACTION_STATUS,
                    "initial_option": out_option,
                    "options": [
                        out_option,
                        _option("Back — route tickets to them again", STATUS_BACK),
                    ],
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_BACK_ON,
                "optional": True,
                "label": {"type": "plain_text", "text": "Back on (optional)"},
                "hint": {
                    "type": "plain_text",
                    "text": "Their first day back. Leave blank for until marked back.",
                },
                "element": {"type": "datepicker", "action_id": ACTION_BACK_ON},
            },
            {
                "type": "input",
                "block_id": BLOCK_COVER,
                "optional": True,
                "label": {"type": "plain_text", "text": "Route their new tickets to (optional)"},
                "hint": {
                    "type": "plain_text",
                    "text": "Leave blank to spread them across the rest of the rotation.",
                },
                "element": {"type": "users_select", "action_id": ACTION_COVER},
            },
            {
                "type": "input",
                "block_id": BLOCK_MOVE_OPEN,
                "optional": True,
                "label": {"type": "plain_text", "text": "Open tickets"},
                "element": {
                    "type": "checkboxes",
                    "action_id": ACTION_MOVE_OPEN,
                    "options": [move_option],
                },
            },
        ],
    }
