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
BLOCK_CAMPAIGN = "campaign"
BLOCK_CAMPAIGN_URL = "campaign_url"
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
ACTION_CAMPAIGN = "campaign_radio"
ACTION_CAMPAIGN_URL = "campaign_url_input"
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

# The "Is part of campaign?" radio value that reveals the campaign-URL field.
CAMPAIGN_YES_VALUE = "yes"
CAMPAIGN_NO_VALUE = "no"

# Sentinel org-dropdown value that means "I want to create a brand-new org
# inline instead of picking an existing one". When this is selected the SE
# fills the new-org fields below (name + channel + owner) and the submit
# handler creates the org before logging the ticket against it.
CREATE_NEW_ORG_VALUE = "__create_new_org__"


# Ticket types the SE can pick at intake. Bug is the default; Config covers
# non-bug SE actions (e.g. enable a feature-flagged integration, verify a
# domain); Product change covers prod improvements / enhancements raised in
# #product (mirrors `TicketType.FEATURE_REQUEST`). FAQ isn't offered here —
# it's reached via the reclassify modal.
_TYPE_LABELS: dict[TicketType, str] = {
    TicketType.BUG: "Bug",
    TicketType.CONFIG: "Configuration",
    TicketType.FEATURE_REQUEST: "Product change",
}


_SOURCE_LABELS: dict[Source, str] = {
    Source.CUSTOMER_CHANNEL: "Customer channel",
    Source.DM: "DM",
    Source.CALL: "Call",
    Source.EMAIL: "Email",
    Source.IN_APP: "In-app",
    Source.TECH_ASSISTANCE: "#userled-support",
    Source.PRODUCT_CHANNEL: "#product",
}


def _sv(state_values: dict[str, Any] | None, block: str, action: str, key: str) -> Any:  # noqa: ANN401
    """Read one value out of a Slack `view.state.values` dict (or None)."""
    if not state_values:
        return None
    return state_values.get(block, {}).get(action, {}).get(key)


def wants_new_org(state_values: dict[str, Any] | None) -> bool:
    """True when the Org dropdown currently sits on "➕ Create new org…".

    Derived from the view's live `state.values` so the block-action handler can
    recompute the reveal from state alone (rather than the triggering action),
    which keeps the org and campaign toggles from clobbering each other on
    re-render.
    """
    selected = _sv(state_values, BLOCK_ORG, ACTION_ORG, "selected_option") or {}
    return str(selected.get("value") or "") == CREATE_NEW_ORG_VALUE


def wants_campaign(state_values: dict[str, Any] | None) -> bool:
    """True when the "Is part of campaign?" radio currently sits on Yes."""
    selected = _sv(state_values, BLOCK_CAMPAIGN, ACTION_CAMPAIGN, "selected_option") or {}
    return str(selected.get("value") or "") == CAMPAIGN_YES_VALUE


def build_view(
    orgs: list[Org],
    *,
    private_metadata: str = "",
    prefill_description: str = "",
    initial_source: Source = Source.DM,
    initial_org_id: str | None = None,
    initial_owner_id: str | None = None,
    show_new_org: bool = False,
    show_campaign: bool = False,
    state_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the intake modal view.

    `show_new_org` toggles the inline "create a new org" fields (name +
    channel + owner). They're hidden by default and only revealed when the SE
    picks "➕ Create new org…" from the Org dropdown — the block-action handler
    re-renders the view via `views.update`. `state_values` is the prior
    `view.state.values`, threaded through on those re-renders so anything the
    SE has already typed is re-seeded as `initial_*` and survives the update.

    `show_campaign` works the same way for the "Is part of campaign?" radio:
    picking Yes reveals a required Campaign URL field (picking No hides it).
    """
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
    # without SE seeding the orgs table first. Picking it reveals the new-org
    # fields (see `show_new_org`).
    org_options.append(
        {
            "text": {"type": "plain_text", "text": "➕ Create new org…"},
            "value": CREATE_NEW_ORG_VALUE,
        }
    )
    # Org selection: prefer what's already in state (round-tripped on update),
    # else pre-select from the invoking channel.
    org_initial = _sv(state_values, BLOCK_ORG, ACTION_ORG, "selected_option") or next(
        (opt for opt in org_options if opt["value"] == initial_org_id), None
    )
    type_options = [
        {"text": {"type": "plain_text", "text": label}, "value": ticket_type.value}
        for ticket_type, label in _TYPE_LABELS.items()
    ]
    type_initial = _sv(state_values, BLOCK_TYPE, ACTION_TYPE, "selected_option") or {
        "text": {"type": "plain_text", "text": _TYPE_LABELS[TicketType.BUG]},
        "value": TicketType.BUG.value,
    }
    source_options = [
        {"text": {"type": "plain_text", "text": label}, "value": source.value}
        for source, label in _SOURCE_LABELS.items()
    ]
    source_initial = _sv(state_values, BLOCK_SOURCE, ACTION_SOURCE, "selected_option") or {
        "text": {"type": "plain_text", "text": _SOURCE_LABELS[initial_source]},
        "value": initial_source.value,
    }

    org_element: dict[str, Any] = {
        "type": "static_select",
        "action_id": ACTION_ORG,
        "placeholder": {"type": "plain_text", "text": "Pick an org"},
        "options": org_options,
    }
    if org_initial is not None:
        org_element["initial_option"] = org_initial

    description_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": ACTION_DESCRIPTION,
        "multiline": True,
    }
    description_initial = _sv(state_values, BLOCK_DESCRIPTION, ACTION_DESCRIPTION, "value") or (
        prefill_description or None
    )
    if description_initial:
        description_element["initial_value"] = description_initial[:2900]

    type_element: dict[str, Any] = {
        "type": "static_select",
        "action_id": ACTION_TYPE,
        "options": type_options,
        "initial_option": type_initial,
    }

    platform_wide_element: dict[str, Any] = {
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
    }
    platform_wide_initial = _sv(
        state_values, BLOCK_PLATFORM_WIDE, ACTION_PLATFORM_WIDE, "selected_options"
    )
    if platform_wide_initial:
        platform_wide_element["initial_options"] = platform_wide_initial

    source_element: dict[str, Any] = {
        "type": "static_select",
        "action_id": ACTION_SOURCE,
        "options": source_options,
        "initial_option": source_initial,
    }

    campaign_element: dict[str, Any] = {
        "type": "radio_buttons",
        "action_id": ACTION_CAMPAIGN,
        "options": [
            {"text": {"type": "plain_text", "text": "Yes"}, "value": CAMPAIGN_YES_VALUE},
            {"text": {"type": "plain_text", "text": "No"}, "value": CAMPAIGN_NO_VALUE},
        ],
    }
    if campaign_initial := _sv(state_values, BLOCK_CAMPAIGN, ACTION_CAMPAIGN, "selected_option"):
        campaign_element["initial_option"] = campaign_initial

    campaign_url_element: dict[str, Any] = {
        "type": "url_text_input",
        "action_id": ACTION_CAMPAIGN_URL,
    }
    if campaign_url_initial := _sv(state_values, BLOCK_CAMPAIGN_URL, ACTION_CAMPAIGN_URL, "value"):
        campaign_url_element["initial_value"] = campaign_url_initial

    summary_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": ACTION_SUMMARY,
        "max_length": 140,
    }
    if summary_initial := _sv(state_values, BLOCK_SUMMARY, ACTION_SUMMARY, "value"):
        summary_element["initial_value"] = summary_initial

    blocking_element: dict[str, Any] = {
        "type": "radio_buttons",
        "action_id": ACTION_BLOCKING,
        "options": [
            {"text": {"type": "plain_text", "text": "Yes"}, "value": "yes"},
            {"text": {"type": "plain_text", "text": "No"}, "value": "no"},
        ],
    }
    if blocking_initial := _sv(state_values, BLOCK_BLOCKING, ACTION_BLOCKING, "selected_option"):
        blocking_element["initial_option"] = blocking_initial

    deadline_element: dict[str, Any] = {"type": "datepicker", "action_id": ACTION_DEADLINE}
    if deadline_initial := _sv(state_values, BLOCK_DEADLINE, ACTION_DEADLINE, "selected_date"):
        deadline_element["initial_date"] = deadline_initial

    affected_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": ACTION_AFFECTED_USER,
    }
    if affected_initial := _sv(state_values, BLOCK_AFFECTED_USER, ACTION_AFFECTED_USER, "value"):
        affected_element["initial_value"] = affected_initial

    replay_element: dict[str, Any] = {"type": "url_text_input", "action_id": ACTION_REPLAY_LINK}
    if replay_initial := _sv(state_values, BLOCK_REPLAY_LINK, ACTION_REPLAY_LINK, "value"):
        replay_element["initial_value"] = replay_initial

    blocks: list[dict[str, Any]] = [
        {
            "type": "input",
            "block_id": BLOCK_TYPE,
            "label": {"type": "plain_text", "text": "Type"},
            "element": type_element,
            "hint": {
                "type": "plain_text",
                "text": (
                    "Bug for something broken. Configuration for a non-bug SE "
                    "action (e.g. enable a feature flag, verify a domain). "
                    "Product change for a prod improvement / enhancement request."
                ),
            },
        },
        {
            "type": "input",
            "block_id": BLOCK_PLATFORM_WIDE,
            "optional": True,
            "label": {"type": "plain_text", "text": "Platform-wide?"},
            "element": platform_wide_element,
            "hint": {
                "type": "plain_text",
                "text": (
                    "Tick if this affects the whole platform, not just one "
                    "customer. Applies to Bug tickets; ignored for "
                    "Configuration and Product change."
                ),
            },
        },
        {
            "type": "input",
            "block_id": BLOCK_ORG,
            "label": {"type": "plain_text", "text": "Org"},
            "element": org_element,
            # Emit a block_action on change so the handler can reveal/hide the
            # new-org fields via views.update.
            "dispatch_action": True,
        },
    ]

    if show_new_org:
        blocks.extend(_new_org_blocks(state_values=state_values, initial_owner_id=initial_owner_id))

    blocks.extend(
        [
            {
                "type": "input",
                "block_id": BLOCK_SOURCE,
                "label": {"type": "plain_text", "text": "Source"},
                "element": source_element,
            },
            {
                "type": "input",
                "block_id": BLOCK_CAMPAIGN,
                "label": {"type": "plain_text", "text": "Is part of campaign?"},
                "element": campaign_element,
                # Emit a block_action on change so the handler can reveal/hide
                # the Campaign URL field via views.update.
                "dispatch_action": True,
            },
        ]
    )

    # Revealed only when the campaign radio is on Yes. Required (not optional):
    # since the block is absent unless revealed, switching back to No removes it,
    # so it can't block a No submit — same rationale as the new-org fields.
    if show_campaign:
        blocks.append(
            {
                "type": "input",
                "block_id": BLOCK_CAMPAIGN_URL,
                "label": {"type": "plain_text", "text": "Campaign URL"},
                "element": campaign_url_element,
                "hint": {
                    "type": "plain_text",
                    "text": "Link to the campaign this ticket relates to.",
                },
            }
        )

    blocks.extend(
        [
            {
                "type": "input",
                "block_id": BLOCK_SUMMARY,
                "label": {"type": "plain_text", "text": "One-line summary"},
                "element": summary_element,
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
                "element": blocking_element,
                "hint": {
                    "type": "plain_text",
                    "text": (
                        "Configuration and Product-change tickets default to P4; "
                        "mark this Yes only if it's urgent (bumps to P2)."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_DEADLINE,
                "label": {"type": "plain_text", "text": "Deadline (if blocking)"},
                "element": deadline_element,
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
                "element": affected_element,
                "optional": True,
            },
            {
                "type": "input",
                "block_id": BLOCK_REPLAY_LINK,
                "label": {"type": "plain_text", "text": "Link"},
                "element": replay_element,
                "optional": True,
            },
        ]
    )

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Log ticket"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def _new_org_blocks(
    *, state_values: dict[str, Any] | None, initial_owner_id: str | None
) -> list[dict[str, Any]]:
    """The three inline "create a new org" inputs, shown only when the SE picks
    "➕ Create new org…". They're required (not optional): since the blocks are
    absent unless revealed, switching back to an existing org removes them, so
    they can't block an existing-org submit. Name + channel are re-validated on
    submit; owner defaults to whoever is logging."""
    name_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": ACTION_NEW_ORG_NAME,
        "max_length": 75,
        "placeholder": {"type": "plain_text", "text": "e.g. Acme Corp"},
    }
    if name_initial := _sv(state_values, BLOCK_NEW_ORG_NAME, ACTION_NEW_ORG_NAME, "value"):
        name_element["initial_value"] = name_initial

    channel_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": ACTION_NEW_ORG_CHANNEL,
        "placeholder": {"type": "plain_text", "text": "e.g. C0123ABCD"},
    }
    if channel_initial := _sv(state_values, BLOCK_NEW_ORG_CHANNEL, ACTION_NEW_ORG_CHANNEL, "value"):
        channel_element["initial_value"] = channel_initial

    owner_element: dict[str, Any] = {
        "type": "users_select",
        "action_id": ACTION_NEW_ORG_OWNER,
        "placeholder": {"type": "plain_text", "text": "Pick the owner (CSM)"},
    }
    owner_initial = (
        _sv(state_values, BLOCK_NEW_ORG_OWNER, ACTION_NEW_ORG_OWNER, "selected_user")
        or initial_owner_id
    )
    if owner_initial:
        owner_element["initial_user"] = owner_initial

    return [
        {
            "type": "context",
            "block_id": "new_org_hint",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        ":new: *New customer* — fill these three and Submit; "
                        "the org is created automatically."
                    ),
                }
            ],
        },
        {
            "type": "input",
            "block_id": BLOCK_NEW_ORG_NAME,
            "label": {"type": "plain_text", "text": "New org — name"},
            "element": name_element,
        },
        {
            "type": "input",
            "block_id": BLOCK_NEW_ORG_CHANNEL,
            "label": {"type": "plain_text", "text": "New org — Slack channel ID"},
            "element": channel_element,
            "hint": {
                "type": "plain_text",
                "text": (
                    "Copy the customer channel's ID (channel name → About → bottom of the pane)."
                ),
            },
        },
        {
            "type": "input",
            "block_id": BLOCK_NEW_ORG_OWNER,
            "label": {"type": "plain_text", "text": "New org — owner (CSM)"},
            "element": owner_element,
            "hint": {
                "type": "plain_text",
                "text": "Defaults to you; change it if someone else owns this customer.",
            },
        },
    ]


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
