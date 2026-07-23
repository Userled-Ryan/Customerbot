from __future__ import annotations

from datetime import date, timedelta
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
    DeadlineTooSoonError,
    parse_csm_intake,
    parse_reclassify,
    parse_resolve,
    parse_se_bug,
)

# A deadline safely past the <48h minimum lead window, computed relative to
# today so the round-trip test doesn't rot as the calendar advances.
_FUTURE_DEADLINE = (date.today() + timedelta(days=30)).isoformat()


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
    campaign: str = "no",
    campaign_url: str = "",
    urgent: bool = False,
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
    campaign_block: dict[str, Any] = {}
    if campaign:
        campaign_block = {
            "selected_option": {
                "value": campaign,
                "text": {"type": "plain_text", "text": campaign},
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
            se_bug.BLOCK_URGENT: {
                se_bug.ACTION_URGENT: {
                    "selected_options": ([{"value": se_bug.URGENT_VALUE}] if urgent else [])
                }
            },
            se_bug.BLOCK_BLOCKING: {se_bug.ACTION_BLOCKING: blocking_block},
            se_bug.BLOCK_DEADLINE: {se_bug.ACTION_DEADLINE: {"selected_date": deadline}},
            se_bug.BLOCK_AFFECTED_USER: {se_bug.ACTION_AFFECTED_USER: {"value": affected_user}},
            se_bug.BLOCK_REPLAY_LINK: {se_bug.ACTION_REPLAY_LINK: {"value": replay_link}},
            se_bug.BLOCK_CAMPAIGN: {se_bug.ACTION_CAMPAIGN: campaign_block},
            se_bug.BLOCK_CAMPAIGN_URL: {se_bug.ACTION_CAMPAIGN_URL: {"value": campaign_url}},
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
            deadline=_FUTURE_DEADLINE,
            affected_user="u@acme.com",
            replay_link="https://r/1",
        )
    )
    assert sub.summary == "Boom"
    assert sub.source == Source.DM
    assert sub.blocking is True
    assert sub.deadline is not None and sub.deadline.isoformat() == _FUTURE_DEADLINE
    assert sub.affected_user == "u@acme.com"
    assert sub.replay_link == "https://r/1"
    assert sub.ticket_type == TicketType.BUG
    assert sub.urgent is False


def test_parse_se_bug_urgent_checkbox() -> None:
    sub = parse_se_bug(_se_view(urgent=True))
    assert sub.urgent is True


def test_parse_se_bug_rejects_deadline_inside_48h() -> None:
    # A blocking deadline tomorrow is inside the 2-day minimum lead window.
    soon = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(DeadlineTooSoonError) as exc:
        parse_se_bug(_se_view(blocking="yes", deadline=soon))
    assert exc.value.block == se_bug.BLOCK_DEADLINE


def test_parse_se_bug_urgent_bypasses_deadline_rule() -> None:
    # Urgent + a sub-48h deadline must not error — urgent drops the deadline.
    soon = (date.today() + timedelta(days=1)).isoformat()
    sub = parse_se_bug(_se_view(blocking="yes", deadline=soon, urgent=True))
    assert sub.urgent is True


def test_parse_se_bug_non_blocking_soon_deadline_not_rejected() -> None:
    # A non-blocking ticket drops its deadline anyway, so no 48h error fires.
    soon = (date.today() + timedelta(days=1)).isoformat()
    sub = parse_se_bug(_se_view(blocking="no", deadline=soon))
    assert sub.deadline is None


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


def test_parse_se_bug_no_campaign_by_default() -> None:
    sub = parse_se_bug(_se_view(campaign="no", campaign_url="https://ignored"))
    assert sub.campaign_url is None


def test_parse_se_bug_campaign_yes_carries_url() -> None:
    sub = parse_se_bug(_se_view(campaign="yes", campaign_url="https://app.userled.io/campaigns/42"))
    assert sub.campaign_url == "https://app.userled.io/campaigns/42"


def test_parse_se_bug_missing_campaign_raises() -> None:
    with pytest.raises(ValueError, match="campaign"):
        parse_se_bug(_se_view(campaign=""))


def test_parse_se_bug_campaign_yes_requires_url() -> None:
    with pytest.raises(ValueError, match="campaign_url"):
        parse_se_bug(_se_view(campaign="yes", campaign_url=""))


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
    assert values == {
        TicketType.BUG.value,
        TicketType.CONFIG.value,
        TicketType.FEATURE_REQUEST.value,
    }
    assert type_block["element"]["initial_option"]["value"] == TicketType.BUG.value


_NEW_ORG_BLOCKS = (
    se_bug.BLOCK_NEW_ORG_NAME,
    se_bug.BLOCK_NEW_ORG_CHANNEL,
    se_bug.BLOCK_NEW_ORG_OWNER,
)


def test_se_bug_view_offers_create_new_org_but_hides_fields_by_default() -> None:
    view = se_bug.build_view([Org(id="acme", name="Acme")], initial_owner_id="U_ME")
    org_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_ORG)
    org_values = [o["value"] for o in org_block["element"]["options"]]
    # Real orgs first, "create new org" sentinel last.
    assert org_values == ["acme", se_bug.CREATE_NEW_ORG_VALUE]
    # The org select dispatches a block_action so we can reveal the fields.
    assert org_block["dispatch_action"] is True
    # New-org fields stay hidden until "Create new org…" is picked.
    block_ids = {b.get("block_id") for b in view["blocks"]}
    assert not any(bid in block_ids for bid in _NEW_ORG_BLOCKS)


def test_se_bug_view_reveals_new_org_fields_when_shown() -> None:
    view = se_bug.build_view(
        [Org(id="acme", name="Acme")], initial_owner_id="U_ME", show_new_org=True
    )
    block_ids = {b.get("block_id") for b in view["blocks"]}
    assert all(bid in block_ids for bid in _NEW_ORG_BLOCKS)
    owner_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_NEW_ORG_OWNER)
    assert owner_block["element"]["initial_user"] == "U_ME"
    # Required (not optional) so the SE can't submit a blank new org.
    for bid in _NEW_ORG_BLOCKS:
        assert next(b for b in view["blocks"] if b["block_id"] == bid).get("optional") is not True


def test_se_bug_view_reinjects_typed_state_on_reveal() -> None:
    """views.update rebuilds the view — already-typed fields must survive."""
    state_values = {
        se_bug.BLOCK_SUMMARY: {se_bug.ACTION_SUMMARY: {"value": "Half-typed summary"}},
        se_bug.BLOCK_DESCRIPTION: {se_bug.ACTION_DESCRIPTION: {"value": "Some detail"}},
        se_bug.BLOCK_ORG: {
            se_bug.ACTION_ORG: {
                "selected_option": {
                    "value": se_bug.CREATE_NEW_ORG_VALUE,
                    "text": {"type": "plain_text", "text": "➕ Create new org…"},
                }
            }
        },
    }
    view = se_bug.build_view(
        [Org(id="acme", name="Acme")], show_new_org=True, state_values=state_values
    )
    summary_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_SUMMARY)
    assert summary_block["element"]["initial_value"] == "Half-typed summary"
    desc_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_DESCRIPTION)
    assert desc_block["element"]["initial_value"] == "Some detail"
    org_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_ORG)
    assert org_block["element"]["initial_option"]["value"] == se_bug.CREATE_NEW_ORG_VALUE


def test_se_bug_view_offers_campaign_radio_but_hides_url_by_default() -> None:
    view = se_bug.build_view([Org(id="acme", name="Acme")])
    campaign_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_CAMPAIGN)
    assert campaign_block["element"]["type"] == "radio_buttons"
    assert {o["value"] for o in campaign_block["element"]["options"]} == {"yes", "no"}
    # Required (no `optional`) and dispatches so we can reveal the URL field.
    assert campaign_block.get("optional") is not True
    assert campaign_block["dispatch_action"] is True
    # URL field stays hidden until Yes is picked.
    block_ids = {b.get("block_id") for b in view["blocks"]}
    assert se_bug.BLOCK_CAMPAIGN_URL not in block_ids


def test_se_bug_view_reveals_campaign_url_when_shown() -> None:
    view = se_bug.build_view([Org(id="acme", name="Acme")], show_campaign=True)
    url_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_CAMPAIGN_URL)
    assert url_block["element"]["type"] == "url_text_input"
    # Required (not optional) so a campaign ticket can't be logged without a URL.
    assert url_block.get("optional") is not True


def test_se_bug_view_reinjects_campaign_state_on_reveal() -> None:
    state_values = {
        se_bug.BLOCK_CAMPAIGN: {
            se_bug.ACTION_CAMPAIGN: {
                "selected_option": {"value": "yes", "text": {"type": "plain_text", "text": "Yes"}}
            }
        },
        se_bug.BLOCK_CAMPAIGN_URL: {
            se_bug.ACTION_CAMPAIGN_URL: {"value": "https://app.userled.io/campaigns/42"}
        },
    }
    view = se_bug.build_view(
        [Org(id="acme", name="Acme")], show_campaign=True, state_values=state_values
    )
    campaign_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_CAMPAIGN)
    assert campaign_block["element"]["initial_option"]["value"] == "yes"
    url_block = next(b for b in view["blocks"] if b["block_id"] == se_bug.BLOCK_CAMPAIGN_URL)
    assert url_block["element"]["initial_value"] == "https://app.userled.io/campaigns/42"
    # The helpers used by the block-action handler agree with the state.
    assert se_bug.wants_campaign(state_values) is True
    assert se_bug.wants_new_org(state_values) is False


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


def test_parse_resolve_code_change_without_pr_link() -> None:
    # A code change (DB migration, config tweak) may ship without a PR.
    ticket_id, resolution_type, pr_link = parse_resolve(
        _resolve_view(resolution="code-change", pr_link="")
    )
    assert ticket_id == 7
    assert resolution_type == ResolutionType.CODE_CHANGE
    assert pr_link is None


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
