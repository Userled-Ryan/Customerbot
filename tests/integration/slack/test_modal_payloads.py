from __future__ import annotations

from typing import Any

import pytest

from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import (
    Source,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.slack.modals import csm_intake, reclassify, se_bug
from customerbot.integration.slack.modals.submission_payload import (
    parse_csm_intake,
    parse_reclassify,
    parse_se_bug,
)


def _csm_view(
    *,
    description: str = "Salesforce broken",
    org_id: str = "acme",
    prod_link: str = "https://app.userled.io/x",
    blocking: str = "no",
    deadline: str | None = None,
    blocking_impact: str = "",
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "values": {
            csm_intake.BLOCK_DESCRIPTION: {csm_intake.ACTION_DESCRIPTION: {"value": description}},
            csm_intake.BLOCK_ORG: {
                csm_intake.ACTION_ORG: {
                    "selected_option": {
                        "value": org_id,
                        "text": {"type": "plain_text", "text": "Acme"},
                    }
                }
            },
            csm_intake.BLOCK_PROD_LINK: {csm_intake.ACTION_PROD_LINK: {"value": prod_link}},
            csm_intake.BLOCK_BLOCKING: {
                csm_intake.ACTION_BLOCKING: {
                    "selected_option": {
                        "value": blocking,
                        "text": {"type": "plain_text", "text": blocking},
                    }
                }
            },
            csm_intake.BLOCK_DEADLINE: {
                csm_intake.ACTION_DEADLINE: (
                    {"selected_date": deadline} if deadline else {"selected_date": None}
                )
            },
            csm_intake.BLOCK_BLOCKING_IMPACT: {
                csm_intake.ACTION_BLOCKING_IMPACT: {"value": blocking_impact}
            },
        }
    }
    return {"state": state}


def _se_view(
    *,
    org_id: str = "acme",
    source: Source = Source.CUSTOMER_CHANNEL,
    summary: str = "Boom",
    description: str = "Repro steps",
    blocking: str = "yes",
    deadline: str | None = None,
    affected_user: str = "",
    replay_link: str = "",
) -> dict[str, Any]:
    blocking_block: dict[str, Any] = {}
    if blocking:
        blocking_block = {
            "selected_option": {
                "value": blocking,
                "text": {"type": "plain_text", "text": blocking},
            }
        }
    state: dict[str, Any] = {
        "values": {
            se_bug.BLOCK_ORG: {
                se_bug.ACTION_ORG: {
                    "selected_option": {
                        "value": org_id,
                        "text": {"type": "plain_text", "text": org_id},
                    }
                }
            },
            se_bug.BLOCK_SOURCE: {
                se_bug.ACTION_SOURCE: {
                    "selected_option": {
                        "value": source.value,
                        "text": {"type": "plain_text", "text": source.value},
                    }
                }
            },
            se_bug.BLOCK_SUMMARY: {se_bug.ACTION_SUMMARY: {"value": summary}},
            se_bug.BLOCK_DESCRIPTION: {se_bug.ACTION_DESCRIPTION: {"value": description}},
            se_bug.BLOCK_BLOCKING: {se_bug.ACTION_BLOCKING: blocking_block},
            se_bug.BLOCK_DEADLINE: {se_bug.ACTION_DEADLINE: {"selected_date": deadline}},
            se_bug.BLOCK_AFFECTED_USER: {se_bug.ACTION_AFFECTED_USER: {"value": affected_user}},
            se_bug.BLOCK_REPLAY_LINK: {se_bug.ACTION_REPLAY_LINK: {"value": replay_link}},
        }
    }
    return {"state": state}


def test_parse_csm_intake_round_trip() -> None:
    sub = parse_csm_intake(
        _csm_view(
            description="Sync broken",
            org_id="acme",
            blocking="yes",
            blocking_impact="Launch Friday",
            deadline="2026-06-01",
        )
    )
    assert sub.description == "Sync broken"
    assert sub.org_id == "acme"
    assert sub.blocking is True
    assert sub.blocking_impact == "Launch Friday"
    assert sub.deadline is not None and sub.deadline.isoformat() == "2026-06-01"


def test_parse_csm_intake_blocking_requires_impact() -> None:
    with pytest.raises(ValueError, match="blocking_impact"):
        parse_csm_intake(_csm_view(blocking="yes", blocking_impact=""))


def test_parse_csm_intake_blocking_no_drops_impact() -> None:
    sub = parse_csm_intake(_csm_view(blocking="no", blocking_impact="ignored"))
    assert sub.blocking_impact is None


def test_parse_csm_intake_missing_description_raises() -> None:
    with pytest.raises(ValueError, match="description"):
        parse_csm_intake(_csm_view(description=""))


def test_parse_se_bug_round_trip() -> None:
    sub = parse_se_bug(
        _se_view(
            summary="Boom",
            description="Repro",
            source=Source.DM,
            blocking="yes",
            deadline="2026-07-01",
            affected_user="u@acme.com",
            replay_link="https://r/1",
        )
    )
    assert sub.summary == "Boom"
    assert sub.source == Source.DM
    assert sub.blocking is True
    assert sub.deadline is not None and sub.deadline.isoformat() == "2026-07-01"
    assert sub.affected_user == "u@acme.com"
    assert sub.replay_link == "https://r/1"


def test_parse_se_bug_not_blocking_drops_deadline() -> None:
    sub = parse_se_bug(_se_view(blocking="no", deadline="2026-07-01"))
    assert sub.blocking is False
    assert sub.deadline is None


def test_parse_se_bug_missing_summary_raises() -> None:
    with pytest.raises(ValueError, match="summary"):
        parse_se_bug(_se_view(summary=""))


def test_parse_se_bug_missing_blocking_raises() -> None:
    with pytest.raises(ValueError, match="blocking"):
        parse_se_bug(_se_view(blocking=""))


def test_modal_view_renders_with_orgs() -> None:
    """Sanity check: build_view returns a valid modal dict when orgs exist."""
    view = csm_intake.build_view([Org(id="acme", name="Acme")])
    assert view["type"] == "modal"
    assert "submit" in view
    org_block = next(b for b in view["blocks"] if b["block_id"] == csm_intake.BLOCK_ORG)
    options = org_block["element"]["options"]
    assert len(options) == 1
    assert options[0]["value"] == "acme"


def test_modal_view_no_orgs_drops_submit_button() -> None:
    view = csm_intake.build_view([])
    assert "submit" not in view
    assert any("No customer orgs" in b.get("text", {}).get("text", "") for b in view["blocks"])


def _reclassify_view(
    *,
    ticket_id: int = 42,
    new_type: str = "config",
    new_subtype: str = "setup-integration",
    reason: str = "customer-specific config issue",
    next_step: str = "walk through webhook setup",
    owner: str = "U_OWNER",
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "values": {
            reclassify.BLOCK_NEW_TYPE: {
                reclassify.ACTION_NEW_TYPE: {
                    "selected_option": {
                        "value": new_type,
                        "text": {"type": "plain_text", "text": new_type},
                    }
                }
            },
            reclassify.BLOCK_NEW_SUBTYPE: {
                reclassify.ACTION_NEW_SUBTYPE: {
                    "selected_option": {
                        "value": new_subtype,
                        "text": {"type": "plain_text", "text": new_subtype},
                    }
                }
            },
            reclassify.BLOCK_REASON: {reclassify.ACTION_REASON: {"value": reason}},
            reclassify.BLOCK_NEXT_STEP: {reclassify.ACTION_NEXT_STEP: {"value": next_step}},
            reclassify.BLOCK_OWNER: {reclassify.ACTION_OWNER: {"selected_user": owner}},
        }
    }
    return {"state": state, "private_metadata": str(ticket_id)}


def test_parse_reclassify_round_trip() -> None:
    sub = parse_reclassify(_reclassify_view())
    assert sub.ticket_id == 42
    assert sub.new_type == TicketType.CONFIG
    assert sub.new_subtype == TicketSubtype.SETUP_INTEGRATION
    assert sub.reason.startswith("customer-specific")
    assert sub.owner_user_id == "U_OWNER"


def test_parse_reclassify_rejects_mismatched_subtype() -> None:
    # FAQ subtype on a Bug type — should be rejected.
    with pytest.raises(ValueError, match="subtype"):
        parse_reclassify(_reclassify_view(new_type="bug", new_subtype="existing-article"))


def test_parse_reclassify_requires_owner() -> None:
    view = _reclassify_view()
    view["state"]["values"][reclassify.BLOCK_OWNER][reclassify.ACTION_OWNER] = {
        "selected_user": None
    }
    with pytest.raises(ValueError, match="owner"):
        parse_reclassify(view)


def test_parse_reclassify_requires_ticket_id_in_private_metadata() -> None:
    view = _reclassify_view()
    view["private_metadata"] = ""
    with pytest.raises(ValueError, match="ticket_id"):
        parse_reclassify(view)
