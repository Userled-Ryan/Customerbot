from __future__ import annotations

from typing import Any

import pytest

from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import (
    ResolutionType,
    Source,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.slack.modals import csm_intake, reclassify, resolve, se_bug
from customerbot.integration.slack.modals.submission_payload import (
    parse_csm_intake,
    parse_reclassify,
    parse_resolve,
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
    ticket_type: str | None = "bug",
    org_id: str = "acme",
    source: Source = Source.CUSTOMER_CHANNEL,
    summary: str = "Boom",
    description: str = "Repro steps",
    blocking: str = "yes",
    deadline: str | None = None,
    affected_user: str = "",
    replay_link: str = "",
    new_org_name: str = "",
    new_org_channel: str = "",
    new_org_owner: str | None = None,
) -> dict[str, Any]:
    blocking_block: dict[str, Any] = {}
    if blocking:
        blocking_block = {
            "selected_option": {
                "value": blocking,
                "text": {"type": "plain_text", "text": blocking},
            }
        }
    type_block: dict[str, Any] = {}
    if ticket_type:
        type_block = {
            se_bug.ACTION_TYPE: {
                "selected_option": {
                    "value": ticket_type,
                    "text": {"type": "plain_text", "text": ticket_type},
                }
            }
        }
    state: dict[str, Any] = {
        "values": {
            se_bug.BLOCK_TYPE: type_block,
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
            se_bug.BLOCK_NEW_ORG_NAME: {se_bug.ACTION_NEW_ORG_NAME: {"value": new_org_name}},
            se_bug.BLOCK_NEW_ORG_CHANNEL: {
                se_bug.ACTION_NEW_ORG_CHANNEL: {"value": new_org_channel}
            },
            se_bug.BLOCK_NEW_ORG_OWNER: {
                se_bug.ACTION_NEW_ORG_OWNER: {"selected_user": new_org_owner}
            },
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
    assert sub.ticket_type == TicketType.BUG


def test_parse_se_bug_config_type() -> None:
    sub = parse_se_bug(_se_view(ticket_type="config"))
    assert sub.ticket_type == TicketType.CONFIG


def test_parse_se_bug_missing_type_defaults_to_bug() -> None:
    sub = parse_se_bug(_se_view(ticket_type=None))
    assert sub.ticket_type == TicketType.BUG


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


def test_parse_se_bug_no_new_org_by_default() -> None:
    sub = parse_se_bug(_se_view(org_id="acme"))
    assert sub.create_new_org is False
    assert sub.new_org_name is None
    assert sub.new_org_channel_id is None
    assert sub.new_org_owner_id is None


def test_parse_se_bug_create_new_org_carries_fields() -> None:
    sub = parse_se_bug(
        _se_view(
            org_id=se_bug.CREATE_NEW_ORG_VALUE,
            new_org_name="Globex",
            new_org_channel="C123",
            new_org_owner="U_OWNER",
        )
    )
    assert sub.create_new_org is True
    assert sub.org_id == se_bug.CREATE_NEW_ORG_VALUE
    assert sub.new_org_name == "Globex"
    assert sub.new_org_channel_id == "C123"
    assert sub.new_org_owner_id == "U_OWNER"


def test_modal_view_renders_with_orgs() -> None:
    """Sanity check: build_view returns a valid modal dict when orgs exist."""
    view = csm_intake.build_view([Org(id="acme", name="Acme")])
    assert view["type"] == "modal"
    assert "submit" in view
    org_block = next(b for b in view["blocks"] if b["block_id"] == csm_intake.BLOCK_ORG)
    options = org_block["element"]["options"]
    assert len(options) == 1
    assert options[0]["value"] == "acme"


def test_se_bug_view_renders_type_dropdown() -> None:
    view = se_bug.build_view([Org(id="acme", name="Acme")])
    type_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_TYPE)
    values = {o["value"] for o in type_block["element"]["options"]}
    assert values == {TicketType.BUG.value, TicketType.CONFIG.value}
    assert type_block["element"]["initial_option"]["value"] == TicketType.BUG.value


def test_se_bug_view_offers_create_new_org_and_fields() -> None:
    view = se_bug.build_view([Org(id="acme", name="Acme")], initial_owner_id="U_ME")
    org_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_ORG)
    org_values = [o["value"] for o in org_block["element"]["options"]]
    # Real orgs first, "create new org" sentinel last.
    assert org_values == ["acme", se_bug.CREATE_NEW_ORG_VALUE]

    block_ids = {b.get("block_id") for b in view["blocks"]}
    assert {
        se_bug.BLOCK_NEW_ORG_NAME,
        se_bug.BLOCK_NEW_ORG_CHANNEL,
        se_bug.BLOCK_NEW_ORG_OWNER,
    } <= block_ids

    owner_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_NEW_ORG_OWNER)
    assert owner_block["element"]["initial_user"] == "U_ME"
    # New-org fields are optional so a normal (existing-org) submit isn't blocked.
    for bid in (
        se_bug.BLOCK_NEW_ORG_NAME,
        se_bug.BLOCK_NEW_ORG_CHANNEL,
        se_bug.BLOCK_NEW_ORG_OWNER,
    ):
        assert next(b for b in view["blocks"] if b["block_id"] == bid)["optional"] is True


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


def _resolve_view(
    *,
    ticket_id: int = 7,
    resolution: str | None = "code-change",
    pr_link: str = "",
) -> dict[str, Any]:
    resolution_block: dict[str, Any] = {}
    if resolution is not None:
        resolution_block = {
            "selected_option": {
                "value": resolution,
                "text": {"type": "plain_text", "text": resolution},
            }
        }
    state: dict[str, Any] = {
        "values": {
            resolve.BLOCK_RESOLUTION: {resolve.ACTION_RESOLUTION: resolution_block},
            resolve.BLOCK_PR_LINK: {resolve.ACTION_PR_LINK: {"value": pr_link}},
        }
    }
    return {"state": state, "private_metadata": str(ticket_id)}


def test_parse_resolve_code_change_with_pr_round_trip() -> None:
    ticket_id, resolution_type, pr_link = parse_resolve(
        _resolve_view(
            ticket_id=7, resolution="code-change", pr_link="https://github.com/x/y/pull/1"
        )
    )
    assert ticket_id == 7
    assert resolution_type == ResolutionType.CODE_CHANGE
    assert pr_link == "https://github.com/x/y/pull/1"


def test_parse_resolve_code_change_requires_pr_link() -> None:
    with pytest.raises(ValueError, match="PR link"):
        parse_resolve(_resolve_view(resolution="code-change", pr_link=""))


def test_parse_resolve_no_code_change_drops_pr_link() -> None:
    ticket_id, resolution_type, pr_link = parse_resolve(
        _resolve_view(resolution="no-code-change", pr_link="https://ignored")
    )
    assert ticket_id == 7
    assert resolution_type == ResolutionType.NO_CODE_CHANGE
    assert pr_link is None


def test_parse_resolve_requires_resolution() -> None:
    with pytest.raises(ValueError, match="resolution"):
        parse_resolve(_resolve_view(resolution=None))


def test_resolve_view_renders_radio_and_pr_input() -> None:
    view = resolve.build_view(ticket_id=99)
    assert view["type"] == "modal"
    assert view["private_metadata"] == "99"
    radio = next(b for b in view["blocks"] if b.get("block_id") == resolve.BLOCK_RESOLUTION)
    assert radio["element"]["type"] == "radio_buttons"
    assert {o["value"] for o in radio["element"]["options"]} == {"no-code-change", "code-change"}
