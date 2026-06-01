"""Slack block-kit builders for SLA + auto-close DMs.

Pure rendering — no I/O. Kept separate from the use cases so the tests
can assert on block shape without spinning up the full scan job.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.tickets.entities import Ticket

_STAGE_LABEL: dict[SLAStage, str] = {
    SLAStage.FIRST_RESPONSE: "first response",
    SLAStage.STATUS_UPDATE: "status update",
    SLAStage.RESOLUTION: "resolution",
    SLAStage.AWAITING_NUDGE_7D: "awaiting customer (7d nudge)",
    SLAStage.AWAITING_NUDGE_3D: "awaiting customer (72h nudge)",
    SLAStage.AWAITING_NUDGE_1D: "awaiting customer (24h nudge)",
}

_STATE_EMOJI: dict[SLAState, str] = {
    SLAState.GREEN: ":large_green_circle:",
    SLAState.AMBER: ":large_orange_circle:",
    SLAState.RED: ":red_circle:",
}


def sla_transition_blocks(
    ticket: Ticket,
    stage: SLAStage,
    new_state: SLAState,
    elapsed: timedelta,
    target: timedelta,
) -> list[dict[str, Any]]:
    emoji = _STATE_EMOJI[new_state]
    state_label = "BREACHED" if new_state == SLAState.RED else new_state.value.upper()
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{emoji} *SLA {state_label}* — {ticket.display_id} "
                    f"(_{ticket.title}_, {ticket.priority.value})\n"
                    f"Stage: *{_STAGE_LABEL[stage]}* · "
                    f"elapsed {_humanize(elapsed)} / target {_humanize(target)}"
                ),
            },
        },
    ]


def csm_pre_close_blocks(
    ticket: Ticket,
    days_until_auto_close: int,
    affected_org_names: list[str],
) -> list[dict[str, Any]]:
    orgs_text = ", ".join(affected_org_names) if affected_org_names else "_no orgs linked_"
    if days_until_auto_close == 1:
        when_label = "tomorrow"
    elif days_until_auto_close == 3:
        when_label = "in 72 hours"
    else:
        when_label = f"in {days_until_auto_close} days"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":hourglass_flowing_sand: *Awaiting customer confirmation* — "
                    f"{ticket.display_id} will auto-close *{when_label}* "
                    f"without confirmation.\n"
                    f"_{ticket.title}_\n"
                    f"Affected orgs: {orgs_text}\n"
                    f"Nudge the customer if you'd like to keep it open."
                ),
            },
        },
    ]


def auto_close_blocks(ticket: Ticket, awaiting_days: int) -> list[dict[str, Any]]:
    """SE-facing auto-close notification.

    Includes the §9e customer-facing draft inline so SE can copy it into the
    customer thread on the way out if they want to acknowledge the silence;
    `comms_drafts.auto_close_note` owns the actual draft text.
    """
    from customerbot.application.tracking.comms_drafts import auto_close_note

    draft = auto_close_note(ticket)
    quoted = "\n".join(f"> {line}" for line in draft.body.splitlines())
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":lock: *Auto-closed* — {ticket.display_id} sat in "
                    f"_Awaiting customer_ for {awaiting_days} days without "
                    f"confirmation and has been moved to *Closed*.\n"
                    f"_{ticket.title}_"
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": quoted},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "_§9e draft above — paste into the customer thread "
                        "if you'd like to flag the close. Auto-close note "
                        "logged in comms history regardless._"
                    ),
                }
            ],
        },
    ]


def _humanize(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"
