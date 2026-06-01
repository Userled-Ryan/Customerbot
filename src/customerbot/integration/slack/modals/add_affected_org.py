"""Add-affected-org modal (plan Chunk 9 — `Add affected org` button)."""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.entities import Org

CALLBACK_ID = "add_affected_org"

BLOCK_ORG = "org"
ACTION_ORG = "org_select"


def build_view(
    orgs: list[Org],
    *,
    private_metadata: str,
    excluded_org_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return the modal view JSON. `private_metadata` carries the ticket id."""
    excluded = excluded_org_ids or set()
    available = [o for o in orgs if o.id not in excluded]
    if not available:
        return _no_orgs_view(private_metadata=private_metadata)

    org_options = [
        {
            "text": {"type": "plain_text", "text": org.name[:75]},
            "value": org.id,
        }
        for org in available[:100]
    ]

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Add affected org"},
        "submit": {"type": "plain_text", "text": "Add"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": BLOCK_ORG,
                "label": {"type": "plain_text", "text": "Org to add"},
                "element": {
                    "type": "static_select",
                    "action_id": ACTION_ORG,
                    "placeholder": {"type": "plain_text", "text": "Pick an org"},
                    "options": org_options,
                },
            },
        ],
    }


def _no_orgs_view(*, private_metadata: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Add affected org"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":information_source: No additional orgs are available — "
                        "either every configured org is already linked, or the "
                        "orgs table is empty."
                    ),
                },
            }
        ],
    }
