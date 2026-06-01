from __future__ import annotations

from typing import Any

import pytest

from customerbot.domain.tickets.entities import Org
from customerbot.domain.tickets.value_objects import Severity, Source
from customerbot.integration.slack.modals import csm_intake, se_bug
from customerbot.integration.slack.modals.submission_payload import (
    parse_csm_intake,
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
    severity: Severity = Severity.BLOCKING,
    affected_user: str = "",
    replay_link: str = "",
) -> dict[str, Any]:
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
            se_bug.BLOCK_SEVERITY: {
                se_bug.ACTION_SEVERITY: {
                    "selected_option": {
                        "value": severity.value,
                        "text": {"type": "plain_text", "text": severity.value},
                    }
                }
            },
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
            severity=Severity.DEGRADED,
            affected_user="u@acme.com",
            replay_link="https://r/1",
        )
    )
    assert sub.summary == "Boom"
    assert sub.source == Source.DM
    assert sub.severity == Severity.DEGRADED
    assert sub.affected_user == "u@acme.com"
    assert sub.replay_link == "https://r/1"


def test_parse_se_bug_missing_summary_raises() -> None:
    with pytest.raises(ValueError, match="summary"):
        parse_se_bug(_se_view(summary=""))


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
