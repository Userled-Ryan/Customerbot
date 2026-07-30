"""Reclassify modal (min-spec §4c, plan Chunk 10).

Fields: `new_type` (Bug/Config/FAQ) · `new_subtype` (depends on type) ·
`reason` (textarea) · `next_step` (textarea) · `owner` (Slack user picker).

Slack's static dropdowns can't change options based on another dropdown's
value within the same view, so v1 renders ALL subtypes in a single
dropdown and validates server-side that the chosen subtype is valid for
the chosen type (rejected via `errors` response if not).
"""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.value_objects import (
    TicketSubtype,
    TicketType,
    subtypes_for,
)

CALLBACK_ID = "reclassify"

BLOCK_NEW_TYPE = "new_type"
BLOCK_NEW_SUBTYPE = "new_subtype"
BLOCK_REASON = "reason"
BLOCK_NEXT_STEP = "next_step"
BLOCK_OWNER = "owner"

ACTION_NEW_TYPE = "new_type_select"
ACTION_NEW_SUBTYPE = "new_subtype_select"
ACTION_REASON = "reason_input"
ACTION_NEXT_STEP = "next_step_input"
ACTION_OWNER = "owner_select"


_TYPE_LABELS: dict[TicketType, str] = {
    TicketType.BUG: "Bug",
    TicketType.CONFIG: "Config",
    TicketType.FAQ: "FAQ",
    TicketType.FEATURE_REQUEST: "Product change",
    TicketType.CSM_HELP: "CSM Help Request",
}


_SUBTYPE_LABELS: dict[TicketSubtype, str] = {
    TicketSubtype.PLATFORM_WIDE: "Bug · platform-wide",
    TicketSubtype.CUSTOMER_SPECIFIC: "Bug · customer-specific",
    TicketSubtype.SETUP_INTEGRATION: "Config · setup-integration",
    TicketSubtype.CUSTOM_FORM: "Config · custom-form",
    TicketSubtype.CONSULTATIVE: "Config · consultative",
    TicketSubtype.REPORTING: "Config · reporting",
    TicketSubtype.EXISTING_ARTICLE: "FAQ · existing-article",
    TicketSubtype.UPDATE_ARTICLE: "FAQ · update-article",
    TicketSubtype.NEEDS_ARTICLE: "FAQ · needs-article",
    TicketSubtype.NEW_CAPABILITY: "Product change · new-capability",
    TicketSubtype.ENHANCEMENT: "Product change · enhancement",
    TicketSubtype.CSM_ASSISTANCE: "CSM Help Request · general assistance",
}


def build_view(
    *,
    ticket_id: int,
    current_type: TicketType,
    current_subtype: TicketSubtype,
) -> dict[str, Any]:
    """Render the reclassify modal. `private_metadata` carries the ticket id."""
    type_options = [
        {"text": {"type": "plain_text", "text": _TYPE_LABELS[t]}, "value": t.value}
        for t in TicketType
    ]
    subtype_options = [
        {"text": {"type": "plain_text", "text": _SUBTYPE_LABELS[s]}, "value": s.value}
        for s in TicketSubtype
    ]
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": str(ticket_id),
        "title": {"type": "plain_text", "text": "Reclassify ticket"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Currently *{_TYPE_LABELS[current_type]} · {current_subtype.value}*."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_NEW_TYPE,
                "label": {"type": "plain_text", "text": "New type"},
                "element": {
                    "type": "static_select",
                    "action_id": ACTION_NEW_TYPE,
                    "options": type_options,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_NEW_SUBTYPE,
                "label": {"type": "plain_text", "text": "New subtype"},
                "element": {
                    "type": "static_select",
                    "action_id": ACTION_NEW_SUBTYPE,
                    "options": subtype_options,
                },
                "hint": {
                    "type": "plain_text",
                    "text": (
                        "Must match the chosen type "
                        "(Bug only allows platform-wide / customer-specific, etc)."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_REASON,
                "label": {"type": "plain_text", "text": "Why reclassify?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_REASON,
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_NEXT_STEP,
                "label": {"type": "plain_text", "text": "Next step"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_NEXT_STEP,
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_OWNER,
                "label": {"type": "plain_text", "text": "Owner"},
                "element": {
                    "type": "users_select",
                    "action_id": ACTION_OWNER,
                    "placeholder": {"type": "plain_text", "text": "Pick a user"},
                },
            },
        ],
    }


def subtype_belongs_to_type(subtype: TicketSubtype, ticket_type: TicketType) -> bool:
    """Return True if `subtype` is one of `subtypes_for(ticket_type)`."""
    return subtype in subtypes_for(ticket_type)
