"""SE / bug intake modal (min-spec §4b)."""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import Source, TicketType

CALLBACK_ID = "se_bug"

BLOCK_TYPE = "ticket_type"
BLOCK_PLATFORM_WIDE = "platform_wide"
BLOCK_ORG = "org"
BLOCK_SOURCE = "source"
BLOCK_SUMMARY = "summary"
BLOCK_DESCRIPTION = "description"
BLOCK_BLOCKING = "blocking"
BLOCK_DEADLINE = "deadline"
BLOCK_AFFECTED_USER = "affected_user"
BLOCK_REPLAY_LINK = "replay_link"

ACTION_TYPE = "ticket_type_select"
ACTION_PLATFORM_WIDE = "platform_wide_check"
ACTION_ORG = "org_select"
ACTION_SOURCE = "source_select"
ACTION_SUMMARY = "summary_input"
ACTION_DESCRIPTION = "description_input"
ACTION_BLOCKING = "blocking_radio"
ACTION_DEADLINE = "deadline_pick"
ACTION_AFFECTED_USER = "affected_user_input"
ACTION_REPLAY_LINK = "replay_link_input"

# The checkbox option value carried in the submission when ticked.
PLATFORM_WIDE_VALUE = "platform_wide"


# Ticket types the SE can pick at intake. Bug is the default; Config covers
# non-bug SE actions (e.g. enable a feature-flagged integration, verify a
# domain). FAQ isn't offered here — it's reached via the reclassify modal.
_TYPE_LABELS: dict[TicketType, str] = {
    TicketType.BUG: "Bug",
    TicketType.CONFIG: "Configuration",
}


_SOURCE_LABELS: dict[Source, str] = {
    Source.CUSTOMER_CHANNEL: "Customer channel",
    Source.DM: "DM",
    Source.CALL: "Call",
    Source.EMAIL: "Email",
    Source.IN_APP: "In-app",
    Source.TECH_ASSISTANCE: "#userled-support",
}


def build_view(
    orgs: list[Org],
    *,
    private_metadata: str = "",
    prefill_description: str = "",
    initial_source: Source = Source.DM,
    initial_org_id: str | None = None,
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
    # Pre-select the org when we could map it from the invoking channel.
    initial_org_option = next((opt for opt in org_options if opt["value"] == initial_org_id), None)
    type_options = [
        {"text": {"type": "plain_text", "text": label}, "value": ticket_type.value}
        for ticket_type, label in _TYPE_LABELS.items()
    ]
    initial_type_option = {
        "text": {"type": "plain_text", "text": _TYPE_LABELS[TicketType.BUG]},
        "value": TicketType.BUG.value,
    }
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

    org_element: dict[str, Any] = {
        "type": "static_select",
        "action_id": ACTION_ORG,
        "placeholder": {"type": "plain_text", "text": "Pick an org"},
        "options": org_options,
    }
    if initial_org_option is not None:
        org_element["initial_option"] = initial_org_option

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
                "block_id": BLOCK_TYPE,
                "label": {"type": "plain_text", "text": "Type"},
                "element": {
                    "type": "static_select",
                    "action_id": ACTION_TYPE,
                    "options": type_options,
                    "initial_option": initial_type_option,
                },
                "hint": {
                    "type": "plain_text",
                    "text": (
                        "Bug for something broken. Configuration for a non-bug SE "
                        "action (e.g. enable a feature flag, verify a domain)."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_PLATFORM_WIDE,
                "optional": True,
                "label": {"type": "plain_text", "text": "Platform-wide?"},
                "element": {
                    "type": "checkboxes",
                    "action_id": ACTION_PLATFORM_WIDE,
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Affects all customers (platform-wide)",
                            },
                            "value": PLATFORM_WIDE_VALUE,
                        }
                    ],
                },
                "hint": {
                    "type": "plain_text",
                    "text": (
                        "Tick if this affects the whole platform, not just one "
                        "customer. Applies to Bug tickets; ignored for Configuration."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_ORG,
                "label": {"type": "plain_text", "text": "Org"},
                "element": org_element,
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
                "label": {"type": "plain_text", "text": "Is this blocking / urgent?"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": ACTION_BLOCKING,
                    "options": [
                        {"text": {"type": "plain_text", "text": "Yes"}, "value": "yes"},
                        {"text": {"type": "plain_text", "text": "No"}, "value": "no"},
                    ],
                },
                "hint": {
                    "type": "plain_text",
                    "text": (
                        "Configuration tickets default to P4; mark this Yes only "
                        "if it's urgent (bumps to P2)."
                    ),
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
