"""Extract values from Slack `view_submission` payloads.

The DTOs (`CSMIntakeSubmission`, `SEBugSubmission`) live in the application
layer at `customerbot.application.intake.submissions` — application code
consumes them without reaching into the Slack integration. This module's
job is the inverse: take a Slack `view` dict and produce the canonical DTO.

Slack delivers modal submissions as a nested dict:

    payload["view"]["state"]["values"][block_id][action_id]["value" | "selected_option" | ...]
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from customerbot.application.intake.submissions import (
    CSMIntakeSubmission,
    ReclassifySubmission,
    SEBugSubmission,
)
from customerbot.domain.tickets.value_objects import (
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.slack.modals import (
    add_affected_org,
    csm_intake,
    reclassify,
    se_bug,
)


def _values(view: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    return view["state"]["values"]


def _plain(values: dict[str, Any], block: str, action: str) -> str:
    raw = values.get(block, {}).get(action, {}).get("value")
    return (raw or "").strip()


def _selected(values: dict[str, Any], block: str, action: str) -> str | None:
    opt = values.get(block, {}).get(action, {}).get("selected_option")
    if not opt:
        return None
    return str(opt["value"])


def _date(values: dict[str, Any], block: str, action: str) -> date | None:
    raw = values.get(block, {}).get(action, {}).get("selected_date")
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").date()


def parse_csm_intake(view: dict[str, Any]) -> CSMIntakeSubmission:
    v = _values(view)
    description = _plain(v, csm_intake.BLOCK_DESCRIPTION, csm_intake.ACTION_DESCRIPTION)
    org_id = _selected(v, csm_intake.BLOCK_ORG, csm_intake.ACTION_ORG)
    prod_link = _plain(v, csm_intake.BLOCK_PROD_LINK, csm_intake.ACTION_PROD_LINK)
    blocking_value = _selected(v, csm_intake.BLOCK_BLOCKING, csm_intake.ACTION_BLOCKING)
    deadline = _date(v, csm_intake.BLOCK_DEADLINE, csm_intake.ACTION_DEADLINE)
    blocking_impact = _plain(v, csm_intake.BLOCK_BLOCKING_IMPACT, csm_intake.ACTION_BLOCKING_IMPACT)

    if not description:
        raise ValueError("description is required")
    if not org_id:
        raise ValueError("org is required")
    if not prod_link:
        raise ValueError("prod_link is required")
    if blocking_value not in ("yes", "no"):
        raise ValueError("blocking is required (yes/no)")

    blocking = blocking_value == "yes"
    if blocking and not blocking_impact:
        raise ValueError("blocking_impact is required when blocking is yes")

    return CSMIntakeSubmission(
        description=description,
        org_id=org_id,
        prod_link=prod_link,
        blocking=blocking,
        deadline=deadline,
        blocking_impact=blocking_impact if blocking else None,
    )


def parse_se_bug(view: dict[str, Any]) -> SEBugSubmission:
    v = _values(view)
    org_id = _selected(v, se_bug.BLOCK_ORG, se_bug.ACTION_ORG)
    source_raw = _selected(v, se_bug.BLOCK_SOURCE, se_bug.ACTION_SOURCE)
    summary = _plain(v, se_bug.BLOCK_SUMMARY, se_bug.ACTION_SUMMARY)
    description = _plain(v, se_bug.BLOCK_DESCRIPTION, se_bug.ACTION_DESCRIPTION)
    severity_raw = _selected(v, se_bug.BLOCK_SEVERITY, se_bug.ACTION_SEVERITY)
    affected_user = _plain(v, se_bug.BLOCK_AFFECTED_USER, se_bug.ACTION_AFFECTED_USER)
    replay_link = _plain(v, se_bug.BLOCK_REPLAY_LINK, se_bug.ACTION_REPLAY_LINK)

    if not org_id:
        raise ValueError("org is required")
    if not source_raw:
        raise ValueError("source is required")
    if not summary:
        raise ValueError("summary is required")
    if not severity_raw:
        raise ValueError("severity is required")

    return SEBugSubmission(
        org_id=org_id,
        source=Source(source_raw),
        summary=summary,
        description=description,
        severity=Severity(severity_raw),
        affected_user=affected_user or None,
        replay_link=replay_link or None,
    )


def _selected_user(values: dict[str, Any], block: str, action: str) -> str | None:
    raw = values.get(block, {}).get(action, {}).get("selected_user")
    return str(raw) if raw else None


def parse_reclassify(view: dict[str, Any]) -> ReclassifySubmission:
    v = _values(view)
    new_type_raw = _selected(v, reclassify.BLOCK_NEW_TYPE, reclassify.ACTION_NEW_TYPE)
    new_subtype_raw = _selected(v, reclassify.BLOCK_NEW_SUBTYPE, reclassify.ACTION_NEW_SUBTYPE)
    reason = _plain(v, reclassify.BLOCK_REASON, reclassify.ACTION_REASON)
    next_step = _plain(v, reclassify.BLOCK_NEXT_STEP, reclassify.ACTION_NEXT_STEP)
    owner = _selected_user(v, reclassify.BLOCK_OWNER, reclassify.ACTION_OWNER)
    raw_metadata = str(view.get("private_metadata") or "").strip()

    if not new_type_raw:
        raise ValueError("new_type is required")
    if not new_subtype_raw:
        raise ValueError("new_subtype is required")
    if not reason:
        raise ValueError("reason is required")
    if not next_step:
        raise ValueError("next_step is required")
    if not owner:
        raise ValueError("owner is required")
    if not raw_metadata:
        raise ValueError("ticket_id missing from private_metadata")
    try:
        ticket_id = int(raw_metadata)
    except ValueError as exc:
        raise ValueError(f"invalid ticket_id in private_metadata: {raw_metadata!r}") from exc

    new_type = TicketType(new_type_raw)
    new_subtype = TicketSubtype(new_subtype_raw)
    if not reclassify.subtype_belongs_to_type(new_subtype, new_type):
        raise ValueError(
            f"subtype {new_subtype.value!r} does not belong to type {new_type.value!r}"
        )

    return ReclassifySubmission(
        ticket_id=ticket_id,
        new_type=new_type,
        new_subtype=new_subtype,
        reason=reason,
        next_step=next_step,
        owner_user_id=owner,
    )


def parse_add_affected_org(view: dict[str, Any]) -> tuple[int, str]:
    """Return `(ticket_id, org_id)` from the add-affected-org submission."""
    v = _values(view)
    org_id = _selected(v, add_affected_org.BLOCK_ORG, add_affected_org.ACTION_ORG)
    raw_metadata = str(view.get("private_metadata") or "").strip()
    if not org_id:
        raise ValueError("org is required")
    if not raw_metadata:
        raise ValueError("ticket_id missing from private_metadata")
    try:
        ticket_id = int(raw_metadata)
    except ValueError as exc:
        raise ValueError(f"invalid ticket_id in private_metadata: {raw_metadata!r}") from exc
    return ticket_id, org_id
