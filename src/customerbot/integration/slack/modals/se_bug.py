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
BLOCK_NEW_ORG_NAME = "new_org_name"
BLOCK_NEW_ORG_CHANNEL = "new_org_channel"
BLOCK_NEW_ORG_OWNER = "new_org_owner"

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
ACTION_NEW_ORG_NAME = "new_org_name_input"
ACTION_NEW_ORG_CHANNEL = "new_org_channel_input"
ACTION_NEW_ORG_OWNER = "new_org_owner_select"

# The checkbox option value carried in the submission when ticked.
PLATFORM_WIDE_VALUE = "platform_wide"

# Sentinel org-dropdown value that means "I want to create a brand-new org
# inline instead of picking an existing one". When this is selected the SE
# fills the new-org fields below (name + channel + owner) and the submit
# handler creates the org before logging the ticket against it.
CREATE_NEW_ORG_VALUE = "__create_new_org__"


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
    initial_owner_id: str | None = None,
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
    # Trailing "create new org" option so CS can onboard a brand-new customer
    # without SE seeding the orgs table first. Picking it activates the
    # new-org fields further down the form.
    org_options.append(
        {
            "text": {"type": "plain_text", "text": "➕ Create new org…"},
            "value": CREATE_NEW_ORG_VALUE,
        }
    )
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

    owner_element: dict[str, Any] = {
        "type": "users_select",
        "action_id": ACTION_NEW_ORG_OWNER,
        "placeholder": {"type": "plain_text", "text": "Pick the owner (CSM)"},
    }
    if initial_owner_id:
        owner_element["initial_user"] = initial_owner_id

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
                "type": "context",
                "block_id": "new_org_hint",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            ":new: *Creating a new org?* Pick "
                            "*➕ Create new org…* above, then fill the three fields "
                            "below. Otherwise leave them blank."
                        ),
                    }
                ],
            },
            {
                "type": "input",
                "block_id": BLOCK_NEW_ORG_NAME,
                "optional": True,
                "label": {"type": "plain_text", "text": "New org — name"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_NEW_ORG_NAME,
                    "max_length": 75,
                    "placeholder": {"type": "plain_text", "text": "e.g. Acme Corp"},
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_NEW_ORG_CHANNEL,
                "optional": True,
                "label": {"type": "plain_text", "text": "New org — Slack channel ID"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_NEW_ORG_CHANNEL,
                    "placeholder": {"type": "plain_text", "text": "e.g. C0123ABCD"},
                },
                "hint": {
                    "type": "plain_text",
                    "text": (
                        "Copy the customer channel's ID (channel name → About → "
                        "bottom of the pane)."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_NEW_ORG_OWNER,
                "optional": True,
                "label": {"type": "plain_text", "text": "New org — owner (CSM)"},
                "element": owner_element,
                "hint": {
                    "type": "plain_text",
                    "text": "Defaults to you; change it if someone else owns this customer.",
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
