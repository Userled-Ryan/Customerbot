"""Tests for the customer-comms draft templates (min-spec §9a–§9e).

Pure-function rendering only — asserted on body text + block shape. The §9b
status-update and §9d confirmation drafts are no longer fired on a timer (the
cadence jobs were removed in favour of the SE-set "Reply needed" flag), but the
template functions remain in `comms_drafts` as the source of truth for the copy,
so they're still covered here.
"""

from __future__ import annotations

from datetime import date, datetime

from customerbot.application.tracking.comms_drafts import (
    Draft,
    auto_close_note,
    initial_ack,
    nudge_for_confirmation,
    resolution,
    status_update,
)
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _ts(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute)


def _bug(
    *,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    priority: Priority = Priority.P2,
    severity: Severity = Severity.BLOCKING,
    title: str = "Checkout broken",
    description: str = "users hang on submit",
    created_at: datetime | None = None,
    first_response_at: datetime | None = None,
    ticket_type: TicketType = TicketType.BUG,
    subtype: TicketSubtype = TicketSubtype.PLATFORM_WIDE,
) -> Ticket:
    return Ticket(
        title=title,
        type=ticket_type,
        subtype=subtype,
        status=status,
        priority=priority,
        severity=severity,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description=description,
        created_at=created_at or _ts(2026, 6, 1, 9, 0),
        first_response_at=first_response_at,
    )


def test_initial_ack_includes_org_name_and_type() -> None:
    ticket = _bug()
    org = Org(id="acme", name="Acme Corp")
    draft = initial_ack(ticket, org)
    assert isinstance(draft, Draft)
    assert "Acme Corp" in draft.headline
    assert "Bug" in draft.body
    assert "[first name]" in draft.body
    # Blocks always lead with a writing-hand banner so SE can distinguish
    # drafts from regular bot DMs.
    blocks = draft.blocks()
    assert blocks[0]["text"]["text"].startswith(":writing_hand:")


def test_initial_ack_truncates_long_description() -> None:
    ticket = _bug(description="x" * 600)
    draft = initial_ack(ticket, None)
    assert "…" in draft.body
    assert "the customer" in draft.headline


def test_status_update_with_internal_note_uses_note() -> None:
    ticket = _bug()
    draft = status_update(ticket, latest_internal_note="Repro confirmed; isolating to billing svc.")
    assert "Repro confirmed" in draft.body
    assert ticket.display_id in draft.body


def test_status_update_without_note_includes_checkpoint() -> None:
    ticket = _bug()
    cp = _ts(2026, 6, 2, 17, 0)
    draft = status_update(ticket, next_checkpoint=cp)
    assert "Still investigating" in draft.body
    # Date roundtrip via strftime — month abbreviation present.
    assert "Jun" in draft.body


def test_resolution_bug_variant() -> None:
    ticket = _bug()
    draft = resolution(ticket, via_hotfix=False)
    assert "shipped a fix" in draft.body
    assert "auto-close in 7 days" in draft.body


def test_resolution_config_variant() -> None:
    ticket = _bug(ticket_type=TicketType.CONFIG, subtype=TicketSubtype.SETUP_INTEGRATION)
    draft = resolution(ticket, via_hotfix=False)
    assert "Setup is complete" in draft.body


def test_resolution_hotfix_variant_mentions_underlying_bug() -> None:
    ticket = _bug()
    draft = resolution(ticket, via_hotfix=True)
    assert "hotfix" in draft.body.lower()
    assert "underlying bug" in draft.body.lower()


def test_nudge_for_confirmation_quotes_auto_close_date() -> None:
    ticket = _bug()
    target_date = date(2026, 6, 15)
    draft = nudge_for_confirmation(ticket, auto_close_at=target_date)
    assert ticket.display_id in draft.body
    assert "auto-close" in draft.body
    # The date should render in the body.
    assert "Jun 2026" in draft.body


def test_auto_close_note_short_and_mentions_30d_reopen() -> None:
    ticket = _bug()
    draft = auto_close_note(ticket)
    assert "30 days" in draft.body
    assert ticket.display_id in draft.body
