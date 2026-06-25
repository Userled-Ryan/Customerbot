"""CSM intake modal (min-spec §4a)."""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.entities import Org

CALLBACK_ID = "csm_intake"

BLOCK_DESCRIPTION = "description"
BLOCK_ORG = "org"
BLOCK_PROD_LINK = "prod_link"
BLOCK_BLOCKING = "blocking"
BLOCK_DEADLINE = "deadline"
BLOCK_BLOCKING_IMPACT = "blocking_impact"

ACTION_DESCRIPTION = "description_input"
ACTION_ORG = "org_select"
ACTION_PROD_LINK = "prod_link_input"
ACTION_BLOCKING = "blocking_radio"
ACTION_DEADLINE = "deadline_pick"
ACTION_BLOCKING_IMPACT = "blocking_impact_input"


def build_view(
    orgs: list[Org],
    *,
    private_metadata: str = "",
) -> dict[str, Any]:
    """Return the modal view JSON. Slack's `views.open` accepts this directly."""

    if not orgs:
        return _no_orgs_view(private_metadata=private_metadata)

    org_options = [
        {
            "text": {"type": "plain_text", "text": org.name[:75]},
            "value": org.id,
        }
        for org in orgs[:100]  # Slack's static_select option cap.
    ]

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Log a ticket"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": BLOCK_DESCRIPTION,
                "label": {"type": "plain_text", "text": "What's going on?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_DESCRIPTION,
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_ORG,
                "label": {"type": "plain_text", "text": "Which customer?"},
                "element": {
                    "type": "static_select",
                    "action_id": ACTION_ORG,
                    "placeholder": {"type": "plain_text", "text": "Pick an org"},
                    "options": org_options,
                },
            },
            {
                "type": "input",
                "block_id": BLOCK_PROD_LINK,
                "label": {
                    "type": "plain_text",
                    "text": "Link to campaign or area in product",
                },
                "element": {
                    "type": "url_text_input",
                    "action_id": ACTION_PROD_LINK,
                },
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
                    "text": "Campaign go-live / deadline (optional)",
                },
                "element": {"type": "datepicker", "action_id": ACTION_DEADLINE},
                "optional": True,
            },
            {
                "type": "input",
                "block_id": BLOCK_BLOCKING_IMPACT,
                "label": {
                    "type": "plain_text",
                    "text": "If blocking — what's the impact? "
                    "(campaign delayed, customer escalating, commitment at risk)",
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": ACTION_BLOCKING_IMPACT,
                },
                "optional": True,
                "hint": {
                    "type": "plain_text",
                    "text": "Required when Blocking = Yes — validated on submit.",
                },
            },
        ],
    }


def _no_orgs_view(*, private_metadata: str) -> dict[str, Any]:
    """Fallback view when the orgs table is empty.

    Submission is disabled (no submit button) so the user can't create a
    ticket without an org — matches the spec's "forced dropdown" principle
    (§1.3 / §4a).
    """
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Log a ticket"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":warning: *No customer orgs are configured yet.* "
                        "An admin needs to seed the orgs table before tickets "
                        "can be logged here.\n\n"
                        "_Reach out to your SE/admin to seed your org._"
                    ),
                },
            }
        ],
    }
