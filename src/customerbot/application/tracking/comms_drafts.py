"""Customer-comms draft templates (min-spec §9, plan Chunk 11).

The bot drafts; SE/CSM sends. This module is the single source of truth
for the five customer-facing templates (§9a–§9e) plus the §9f internal
alert (which lives in `application/tracking/reclassify.py` because it's
emitted from a different surface).

Every public function here is **pure** — given the same inputs, it
produces the same draft text and the same Block-Kit blocks. No I/O,
no clocks. Callers (the intake pipeline, the resolve/auto-close jobs,
the §9d nudge job, the §9b cadence job) are responsible for picking
*when* to render and DM each draft; we only own *what* the draft says.

The frozen `Draft` value carries both:

- `body` — the raw markdown text SE would paste into the customer
  thread. Stored on `pending_reclassify_sends.draft_text` for the §9f
  case and reproduced verbatim in the DM, so SE sees exactly what
  they'd be pasting.
- `blocks` — the rendering used for the DM that surfaces this draft
  to SE. Always includes a `:writing_hand:` banner so SE can
  visually distinguish a draft from a regular bot DM.

Customer first names are placeholdered as `[first name]` — the bot
can't reliably resolve them and asking SE to fill in two characters
is cheaper than getting the wrong name in front of a customer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import Priority, TicketType


@dataclass(frozen=True)
class Draft:
    """A bot-drafted customer-facing message awaiting SE review."""

    headline: str
    body: str

    def blocks(self) -> list[dict[str, Any]]:
        """Render as Block-Kit blocks for DMing SE."""
        quoted = "\n".join(f"> {line}" if line else ">" for line in self.body.splitlines())
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": self.headline},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": quoted},
            },
        ]


# --- §9a Initial acknowledgement ---------------------------------------------


_NEXT_STEP_BY_TYPE: dict[TicketType, str] = {
    TicketType.BUG: "investigate and report back",
    TicketType.CONFIG: "get back to you with options",
    TicketType.FAQ: "share the relevant doc",
}


def initial_ack(ticket: Ticket, org: Org | None) -> Draft:
    """§9a — customer-channel initial acknowledgement."""
    customer_name = org.name if org is not None else "the customer"
    next_step = _NEXT_STEP_BY_TYPE.get(ticket.type, "follow up")
    context = (ticket.description or "(see thread)").strip()
    if len(context) > 300:
        context = context[:299] + "…"
    body = (
        f"Hi [first name],\n\n"
        f"Thanks for flagging — we've logged this on our side as a "
        f"{ticket.type.value.title()} and I'll {next_step} shortly.\n\n"
        f"Quick context if helpful: {context}\n\n"
        f"I'll keep this thread updated."
    )
    headline = (
        f":writing_hand: *Initial-ack draft* — {ticket.display_id} "
        f"(send to {customer_name} when you're ready)"
    )
    return Draft(headline=headline, body=body)


# --- §9b Status update (cadence-driven) --------------------------------------


def status_update(
    ticket: Ticket,
    *,
    latest_internal_note: str | None = None,
    next_checkpoint: datetime | None = None,
) -> Draft:
    """§9b — periodic status update for an in-progress ticket.

    If `latest_internal_note` is provided, it becomes the body; otherwise
    we default to the spec's "Still investigating…" phrasing with the
    next-checkpoint hint when known.
    """
    if latest_internal_note and latest_internal_note.strip():
        update_line = latest_internal_note.strip()
    elif next_checkpoint is not None:
        update_line = (
            f"Still investigating — I'll have more for you by "
            f"{next_checkpoint.strftime('%a %d %b, %H:%M UTC')}."
        )
    else:
        update_line = "Still investigating — I'll have more for you shortly."
    body = f"Quick update on {ticket.display_id}:\n\n{update_line}"
    headline = (
        f":writing_hand: *Status-update draft* — {ticket.display_id} (send when you're ready)"
    )
    return Draft(headline=headline, body=body)


# --- §9c Resolution / awaiting confirmation ----------------------------------


def resolution(ticket: Ticket, *, via_hotfix: bool = False) -> Draft:
    """§9c — resolution / awaiting-confirmation draft.

    `via_hotfix=True` produces the hotfix variant (mentions a workaround
    is live, permanent fix still in progress). The bug-vs-config split in
    the spec is folded into a single body that works for both — the
    customer mostly cares about whether they can stop hitting the issue
    today.
    """
    if via_hotfix:
        body = (
            f"Hi [first name],\n\n"
            f"We've shipped a hotfix for {ticket.title} — it should be "
            f"live for you now. Could you give it a quick check and let "
            f"me know it's looking right on your side?\n\n"
            f"We're keeping the underlying bug open on our engineering "
            f"side so we don't have to revisit this in future."
        )
        headline = (
            f":zap: *Resolution draft (hotfix)* — {ticket.display_id}, ready to send when you are."
        )
    elif ticket.type == TicketType.CONFIG:
        body = (
            f"Hi [first name],\n\n"
            f"Setup is complete on our side for {ticket.title}. "
            f"Let us know if this matches what you needed, or if there's "
            f"anything to adjust. If we don't hear back, we'll close this "
            f"out in 7 days."
        )
        headline = (
            f":white_check_mark: *Resolution draft* — {ticket.display_id}, "
            f"ready to send when you are."
        )
    else:
        body = (
            f"Hi [first name],\n\n"
            f"We've shipped a fix for {ticket.title}. Could you confirm "
            f"whether you're still seeing the issue? If you're no longer "
            f"hitting it, we'll close this out — and if we don't hear "
            f"back we'll auto-close in 7 days."
        )
        headline = (
            f":white_check_mark: *Resolution draft* — {ticket.display_id}, "
            f"ready to send when you are."
        )
    return Draft(headline=headline, body=body)


# --- §9d Nudge for confirmation (24h, 72h, 7d) -------------------------------


def nudge_for_confirmation(ticket: Ticket, *, auto_close_at: date | datetime) -> Draft:
    """§9d — nudge the customer to confirm before auto-close.

    `auto_close_at` is rendered into the body so SE doesn't have to compute
    the date themselves. Accepts either a `date` or `datetime`; datetimes
    render as a date (the customer doesn't care about the minute).
    """
    close_date = auto_close_at.date() if isinstance(auto_close_at, datetime) else auto_close_at
    body = (
        f"Just checking back on {ticket.display_id} — are you good to "
        f"close this out?\n\n"
        f"If we don't hear back, we'll auto-close on "
        f"{close_date.strftime('%a %d %b %Y')}."
    )
    headline = f":bell: *Nudge draft* — {ticket.display_id}, customer hasn't confirmed yet."
    return Draft(headline=headline, body=body)


# --- §9e Auto-close note -----------------------------------------------------


def auto_close_note(ticket: Ticket) -> Draft:
    """§9e — note delivered to the customer after the bot auto-closes."""
    body = (
        f"Closing {ticket.display_id} pending response. "
        f"Reply anytime in the next 30 days and we'll reopen it."
    )
    headline = (
        f":lock: *Auto-close note* — {ticket.display_id} closed after "
        f"7 days awaiting customer; draft below if you'd like to flag it."
    )
    return Draft(headline=headline, body=body)


# --- Helpers ----------------------------------------------------------------


def auto_close_date(awaiting_entered_at: datetime, *, auto_close_days: int = 7) -> datetime:
    """Compute when an awaiting ticket will auto-close.

    Lives here so §9d's `auto_close_at` argument can be derived in one place;
    `auto_close.py` uses the same `auto_close_days` default (Chunk 8).
    """
    return awaiting_entered_at + timedelta(days=auto_close_days)


def next_status_update_checkpoint(
    ticket: Ticket,
    *,
    target_status_update_hours: int | None,
    last_drafted_at: datetime | None,
    now: datetime,
) -> datetime | None:
    """When the *next* §9b draft should be DM'd to SE, or None if uncommitted.

    Driven by the SLA tier's `status_update_hours`. Returns None when the
    priority tier has no committed cadence (e.g. P4 in default config).
    """
    if target_status_update_hours is None:
        return None
    anchor = last_drafted_at or ticket.first_response_at or ticket.created_at
    return anchor + timedelta(hours=target_status_update_hours)


def is_status_update_due(
    ticket: Ticket,
    *,
    target_status_update_hours: int | None,
    last_drafted_at: datetime | None,
    now: datetime,
) -> bool:
    """True when `now >= next_status_update_checkpoint(...)`, false otherwise."""
    checkpoint = next_status_update_checkpoint(
        ticket,
        target_status_update_hours=target_status_update_hours,
        last_drafted_at=last_drafted_at,
        now=now,
    )
    if checkpoint is None:
        return False
    return now >= checkpoint


# Silence unused-import warnings if Priority becomes needed by callers
# through this module (kept for forward-compat with future tier-specific
# tweaks to status-update copy).
_ = Priority
