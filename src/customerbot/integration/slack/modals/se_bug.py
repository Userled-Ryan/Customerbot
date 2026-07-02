"""SE / bug intake modal (min-spec §4b)."""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import Source

CALLBACK_ID = "se_bug"

BLOCK_ORG = "org"
BLOCK_SOURCE = "source"
BLOCK_SUMMARY = "summary"
BLOCK_DESCRIPTION = "description"
BLOCK_BLOCKING = "blocking"
BLOCK_DEADLINE = "deadline"
BLOCK_AFFECTED_USER = "affected_user"
BLOCK_REPLAY_LINK = "replay_link"

ACTION_ORG = "org_select"
ACTION_SOURCE = "source_select"
ACTION_SUMMARY = "summary_input"
ACTION_DESCRIPTION = "description_input"
ACTION_BLOCKING = "blocking_radio"
ACTION_DEADLINE = "deadline_pick"
ACTION_AFFECTED_USER = "affected_user_input"
ACTION_REPLAY_LINK = "replay_link_input"


_SOURCE_LABELS: dict[Source, str] = {
    Source.CUSTOMER_CHANNEL: "Customer channel",
    Source.DM: "DM",
    Source.CALL: "Call",
    Source.EMAIL: "Email",
    Source.IN_APP: "In-app",
    Source.TECH_ASSISTANCE: "#tech-assistance",
}


def build_view(
    orgs: list[Org],
    *,
    private_metadata: str = "",
    prefill_description: str = "",
    initial_source: Source = Source.DM,
) -> dict[str, Any]:
    if not orgs:
        return _no_orgs_view(private_metadata=private_metadata)

    org_options = [
        {
            "text": {"type": "plain_text", "text": org.name[:75]},
            "value": org.id,
        }
        for org in orgs[:100]
    ]
    source_options = [
        {"text": {"type": "plain_text", "text": label}, "value": source.value}
        for source, label in _SOURCE_LABELS.items()
    ]
    initial_source_option = {
        "text": {"type": "plain_text", "text": _SOURCE_LABELS[initial_source]},
        "value": initial_source.value,
    }

    description_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": ACTION_DESCRIPTION,
        "multiline": True,
    }
    if prefill_description:
        description_element["initial_value"] = prefill_description[:2900]

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Log ticket"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": BLOCK_ORG,
                "label": {"type": "plain_text", "text": "Org"},
                "element": {
                    "type": "static_select",
                    "action_id": ACTION_ORG,
                    "placeholder": {"type": "plain_text", "text": "Pick an org"},
                    "options": org_options,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_SOURCE,
                "label": {"type": "plain_text", "text": "Source"},
                "element": {
                    "type": "static_select",
                    "action_id": ACTION_SOURCE,
                    "options": source_options,
                    "initial_option": initial_source_option,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_SUMMARY,
                "label": {"type": "plain_text", "text": "One-line summary"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_SUMMARY,
                    "max_length": 140,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_DESCRIPTION,
                "label": {"type": "plain_text", "text": "Description"},
                "element": description_element,
                "optional": True,
            },
            {
                "type": "input",
                "block_id": BLOCK_BLOCKING,
                "label": {"type": "plain_text", "text": "Is this blocking?"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": ACTION_BLOCKING,
                    "options": [
                        {"text": {"type": "plain_text", "text": "Yes"}, "value": "yes"},
                        {"text": {"type": "plain_text", "text": "No"}, "value": "no"},
                    ],
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_DEADLINE,
                "label": {
                    "type": "plain_text",
                    "text": "Deadline (if blocking)",
                },
                "element": {"type": "datepicker", "action_id": ACTION_DEADLINE},
                "optional": True,
                "hint": {
                    "type": "plain_text",
                    "text": "When does this need to be fixed by? Leave blank if not blocking.",
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_AFFECTED_USER,
                "label": {
                    "type": "plain_text",
                    "text": "Affected user in customer org (email or name)",
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_AFFECTED_USER,
                },
                "optional": True,
            },
            {
                "type": "input",
                "block_id": BLOCK_REPLAY_LINK,
                "label": {"type": "plain_text", "text": "Link"},
                "element": {"type": "url_text_input", "action_id": ACTION_REPLAY_LINK},
                "optional": True,
            },
        ],
    }


def _no_orgs_view(*, private_metadata: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Log ticket"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":warning: *No customer orgs are configured yet.* "
                        "Admin must seed the orgs table before tickets can be logged."
                    ),
                },
            }
        ],
    }
