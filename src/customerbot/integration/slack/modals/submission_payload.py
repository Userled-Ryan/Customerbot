"""Extract values from Slack `view_submission` payloads.

The DTOs (`CSMIntakeSubmission`, `SEBugSubmission`) live in the application
layer at `customerbot.application.intake.submissions` — application code
consumes them without reaching into the Slack integration. This module's
job is the inverse: take a Slack `view` dict and produce the canonical DTO.

Slack delivers modal submissions as a nested dict:

    payload["view"]["state"]["values"][block_id][action_id]["value" | "selected_option" | ...]
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from customerbot.application.intake.submissions import (
    CSMIntakeSubmission,
    ReclassifySubmission,
    SEBugSubmission,
)
from customerbot.domain.tickets.value_objects import (
    ResolutionType,
    Source,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.slack.modals import (
    add_affected_org,
    csm_intake,
    link_ticket,
    reclassify,
    report_range,
    resolve,
    se_bug,
    set_deadline,
    set_stakeholder,
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


def _checkbox_selected(values: dict[str, Any], block: str, action: str, option_value: str) -> bool:
    opts = values.get(block, {}).get(action, {}).get("selected_options") or []
    return any(str(opt.get("value")) == option_value for opt in opts)


def parse_csm_intake(view: dict[str, Any]) -> CSMIntakeSubmission:
    # DORMANT (2026-07-02): CSM intake modal retired — see csm_intake.py header.
    # REMOVE with the rest of that path if we don't revert.
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
    type_raw = _selected(v, se_bug.BLOCK_TYPE, se_bug.ACTION_TYPE)
    org_id = _selected(v, se_bug.BLOCK_ORG, se_bug.ACTION_ORG)
    source_raw = _selected(v, se_bug.BLOCK_SOURCE, se_bug.ACTION_SOURCE)
    summary = _plain(v, se_bug.BLOCK_SUMMARY, se_bug.ACTION_SUMMARY)
    description = _plain(v, se_bug.BLOCK_DESCRIPTION, se_bug.ACTION_DESCRIPTION)
    blocking_value = _selected(v, se_bug.BLOCK_BLOCKING, se_bug.ACTION_BLOCKING)
    deadline = _date(v, se_bug.BLOCK_DEADLINE, se_bug.ACTION_DEADLINE)
    affected_user = _plain(v, se_bug.BLOCK_AFFECTED_USER, se_bug.ACTION_AFFECTED_USER)
    replay_link = _plain(v, se_bug.BLOCK_REPLAY_LINK, se_bug.ACTION_REPLAY_LINK)
    campaign_value = _selected(v, se_bug.BLOCK_CAMPAIGN, se_bug.ACTION_CAMPAIGN)
    campaign_url = _plain(v, se_bug.BLOCK_CAMPAIGN_URL, se_bug.ACTION_CAMPAIGN_URL)
    new_org_name = _plain(v, se_bug.BLOCK_NEW_ORG_NAME, se_bug.ACTION_NEW_ORG_NAME)
    new_org_channel = _plain(v, se_bug.BLOCK_NEW_ORG_CHANNEL, se_bug.ACTION_NEW_ORG_CHANNEL)
    new_org_owner = _selected_user(v, se_bug.BLOCK_NEW_ORG_OWNER, se_bug.ACTION_NEW_ORG_OWNER)

    if not org_id:
        raise ValueError("org is required")
    create_new_org = org_id == se_bug.CREATE_NEW_ORG_VALUE
    if not source_raw:
        raise ValueError("source is required")
    if not summary:
        raise ValueError("summary is required")
    if blocking_value not in ("yes", "no"):
        raise ValueError("blocking is required (yes/no)")
    if campaign_value not in ("yes", "no"):
        raise ValueError("campaign is required (yes/no)")

    blocking = blocking_value == "yes"
    # Campaign URL is only carried when the SE answered Yes; the modal reveals a
    # required URL field in that case (Slack enforces it), so a Yes with a blank
    # URL shouldn't reach here — but guard anyway.
    is_campaign = campaign_value == "yes"
    if is_campaign and not campaign_url:
        raise ValueError("campaign_url is required when campaign is yes")
    # Missing type (older view payloads) falls back to Bug — the modal's
    # dropdown always seeds Bug, so this only guards against a malformed state.
    ticket_type = TicketType(type_raw) if type_raw else TicketType.BUG
    platform_wide = _checkbox_selected(
        v, se_bug.BLOCK_PLATFORM_WIDE, se_bug.ACTION_PLATFORM_WIDE, se_bug.PLATFORM_WIDE_VALUE
    )

    return SEBugSubmission(
        org_id=org_id,
        source=Source(source_raw),
        summary=summary,
        description=description,
        blocking=blocking,
        deadline=deadline if blocking else None,
        affected_user=affected_user or None,
        replay_link=replay_link or None,
        campaign_url=campaign_url if is_campaign else None,
        ticket_type=ticket_type,
        platform_wide=platform_wide,
        create_new_org=create_new_org,
        new_org_name=new_org_name or None,
        new_org_channel_id=new_org_channel or None,
        new_org_owner_id=new_org_owner,
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


def parse_set_deadline(view: dict[str, Any]) -> tuple[int, date | None]:
    """Return `(ticket_id, deadline_or_none)` from the set-deadline submission."""
    v = _values(view)
    picked = _date(v, set_deadline.BLOCK_DEADLINE, set_deadline.ACTION_DEADLINE)
    raw_metadata = str(view.get("private_metadata") or "").strip()
    if not raw_metadata:
        raise ValueError("ticket_id missing from private_metadata")
    try:
        ticket_id = int(raw_metadata)
    except ValueError as exc:
        raise ValueError(f"invalid ticket_id in private_metadata: {raw_metadata!r}") from exc
    return ticket_id, picked


def parse_set_stakeholder(view: dict[str, Any]) -> tuple[int, dict[str, str | None]]:
    """Return `(ticket_id, {org_id: csm_user_id_or_None})` from the submission.

    Only orgs whose picker block is present in the payload are included; a
    picker left blank yields `None` (clear the org's CSM).
    """
    v = _values(view)
    assignments: dict[str, str | None] = {}
    for block_id, block in v.items():
        org_id = set_stakeholder.org_id_from_block(block_id)
        if org_id is None:
            continue
        action_id = set_stakeholder.action_id_for(org_id)
        selected = block.get(action_id, {}).get("selected_user")
        assignments[org_id] = str(selected) if selected else None
    raw_metadata = str(view.get("private_metadata") or "").strip()
    if not raw_metadata:
        raise ValueError("ticket_id missing from private_metadata")
    try:
        ticket_id = int(raw_metadata)
    except ValueError as exc:
        raise ValueError(f"invalid ticket_id in private_metadata: {raw_metadata!r}") from exc
    return ticket_id, assignments


def parse_resolve(view: dict[str, Any]) -> tuple[int, ResolutionType, str | None]:
    """Return `(ticket_id, resolution_type, pr_link)` from the resolve modal.

    The PR link is always optional — some code changes (DB migrations, config
    tweaks) ship without a PR — so only the resolution radio and a well-formed
    ticket id are required.
    """
    v = _values(view)
    resolution_raw = _selected(v, resolve.BLOCK_RESOLUTION, resolve.ACTION_RESOLUTION)
    pr_link = _plain(v, resolve.BLOCK_PR_LINK, resolve.ACTION_PR_LINK) or None
    raw_metadata = str(view.get("private_metadata") or "").strip()

    if not resolution_raw:
        raise ValueError("resolution is required")
    if not raw_metadata:
        raise ValueError("ticket_id missing from private_metadata")
    try:
        ticket_id = int(raw_metadata)
    except ValueError as exc:
        raise ValueError(f"invalid ticket_id in private_metadata: {raw_metadata!r}") from exc

    resolution_type = ResolutionType(resolution_raw)
    # A no-code-change resolution carries no PR link even if one was typed.
    if resolution_type == ResolutionType.NO_CODE_CHANGE:
        pr_link = None
    return ticket_id, resolution_type, pr_link


def parse_report_range(view: dict[str, Any]) -> tuple[str, str, date, date]:
    """Return `(channel_id, user_id, start, end)` from the report-range modal.

    `private_metadata` carries the invoking `{channel_id, user_id}` as JSON so
    the handler can post the ephemeral report back to where `/report` was run.
    Raises `ValueError` on a missing date or an inverted range.
    """
    v = _values(view)
    start = _date(v, report_range.BLOCK_START, report_range.ACTION_START)
    end = _date(v, report_range.BLOCK_END, report_range.ACTION_END)
    if start is None:
        raise ValueError("start date is required")
    if end is None:
        raise ValueError("end date is required")
    if start > end:
        raise ValueError("The start date must be on or before the end date.")
    raw_metadata = str(view.get("private_metadata") or "").strip()
    try:
        meta = json.loads(raw_metadata)
        channel_id = str(meta["channel_id"])
        user_id = str(meta["user_id"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"invalid report metadata: {raw_metadata!r}") from exc
    return channel_id, user_id, start, end


def parse_link_thread(view: dict[str, Any]) -> tuple[str, str, int]:
    """Return `(channel_id, thread_ts, target_ticket_id)` from the link modal.

    `private_metadata` carries `channel_id|thread_ts`; the ticket comes from the
    static-select. Raises `ValueError` on missing/malformed pieces.
    """
    v = _values(view)
    ticket_raw = _selected(v, link_ticket.BLOCK_TICKET, link_ticket.ACTION_TICKET)
    raw_metadata = str(view.get("private_metadata") or "").strip()
    if not ticket_raw:
        raise ValueError("ticket is required")
    channel_id, _, thread_ts = raw_metadata.partition("|")
    if not channel_id or not thread_ts:
        raise ValueError(f"invalid thread metadata: {raw_metadata!r}")
    try:
        target_ticket_id = int(ticket_raw)
    except ValueError as exc:
        raise ValueError(f"invalid ticket_id: {ticket_raw!r}") from exc
    return channel_id, thread_ts, target_ticket_id


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
