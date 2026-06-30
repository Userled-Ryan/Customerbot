"""Set-stakeholder modal — reassign the CSM(s) for a ticket's affected org(s).

Stakeholders on a card are derived from each affected org's `csm_user_id`
(there's no per-ticket stakeholder). So "updating the stakeholder" means
re-pointing the org's CSM, which is why the change *sticks*: every current
and future ticket touching that org picks up the new CSM automatically.

One `users_select` per affected org, each pre-filled with the org's current
CSM. Action/block ids are suffixed with the org id so they stay unique within
the view (duplicate action_ids → `invalid_blocks` → silently-dropped submit).
`private_metadata` carries the ticket id so the submission handler can route
without state lookups.
"""

from __future__ import annotations

from typing import Any

CALLBACK_ID = "set_stakeholder"

# block/action ids carry the org id as a suffix so each picker is unique.
_BLOCK_PREFIX = "stakeholder_org::"
_ACTION_PREFIX = "stakeholder_csm::"


def block_id_for(org_id: str) -> str:
    return f"{_BLOCK_PREFIX}{org_id}"


def action_id_for(org_id: str) -> str:
    return f"{_ACTION_PREFIX}{org_id}"


def org_id_from_block(block_id: str) -> str | None:
    """Inverse of `block_id_for` — `None` for blocks we didn't render."""
    if not block_id.startswith(_BLOCK_PREFIX):
        return None
    return block_id[len(_BLOCK_PREFIX) :]


def build_view(
    *,
    ticket_id: int,
    orgs: list[tuple[str, str, str | None]],
) -> dict[str, Any]:
    """Return the modal view JSON.

    `orgs` is `[(org_id, org_name, current_csm_user_id_or_None), ...]` for the
    ticket's affected orgs. `private_metadata` carries the ticket id.
    """
    if not orgs:
        return _no_orgs_view(ticket_id=ticket_id)

    blocks: list[dict[str, Any]] = [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "_The stakeholder is the org's CSM — changing it here "
                        "updates that org everywhere, so it sticks for future "
                        "tickets too. Leave a picker blank and Save to clear it._"
                    ),
                }
            ],
        }
    ]
    for org_id, org_name, current_csm in orgs:
        element: dict[str, Any] = {
            "type": "users_select",
            "action_id": action_id_for(org_id),
            "placeholder": {"type": "plain_text", "text": "Pick a stakeholder"},
        }
        if current_csm:
            element["initial_user"] = current_csm
        blocks.append(
            {
                "type": "input",
                "block_id": block_id_for(org_id),
                "label": {"type": "plain_text", "text": org_name[:75]},
                "element": element,
                "optional": True,
            }
        )

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": str(ticket_id),
        "title": {"type": "plain_text", "text": "Set stakeholder"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def _no_orgs_view(*, ticket_id: int) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": str(ticket_id),
        "title": {"type": "plain_text", "text": "Set stakeholder"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":information_source: This ticket has no affected orgs, so "
                        "there's no stakeholder to set. Add an affected org first."
                    ),
                },
            }
        ],
    }
