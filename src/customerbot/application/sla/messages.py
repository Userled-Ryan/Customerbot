"""Slack block-kit builders for the auto-close / pre-close DMs.

Pure rendering — no I/O. Kept separate from the use cases so the tests
can assert on block shape without spinning up the full job.

(The per-transition SLA escalation card lived here too; it was removed when
the SE moved to a single twice-daily open-tickets digest. The SLA clocks still
tick silently — see `application/sla/scan.py` — they just no longer render a
notification.)
"""

from __future__ import annotations

from typing import Any

from customerbot.domain.tickets.entities import Ticket


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
