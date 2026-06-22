from __future__ import annotations

from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.linear.mapping import (
    InboundIntent,
    build_issue_description,
    linear_state_to_inbound_intent,
    ticket_priority_to_linear,
    ticket_to_linear_state,
)


def _ticket(**kw: object) -> Ticket:
    base: dict[str, object] = dict(
        id=7,
        title="Publishing fails on Safari",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="Crashes on iOS 18.",
    )
    base.update(kw)
    return Ticket(**base)  # type: ignore[arg-type]


def test_forward_state_mapping_by_status() -> None:
    assert ticket_to_linear_state(TicketStatus.NEW, None) == LinearWorkflowState.TRIAGE
    assert ticket_to_linear_state(TicketStatus.IN_PROGRESS, None) == LinearWorkflowState.IN_PROGRESS
    assert (
        ticket_to_linear_state(TicketStatus.AWAITING_CUSTOMER, None)
        == LinearWorkflowState.AWAITING_CUSTOMER
    )
    assert ticket_to_linear_state(TicketStatus.RESOLVED, None) == LinearWorkflowState.DONE
    assert ticket_to_linear_state(TicketStatus.CLOSED, None) == LinearWorkflowState.DONE


def test_dev_lane_new_is_in_progress_not_triage() -> None:
    # A ticket handed to dev is being worked, so it shows as In Progress.
    assert (
        ticket_to_linear_state(TicketStatus.NEW, Lane.DEV_ACTION)
        == LinearWorkflowState.IN_PROGRESS
    )


def test_reverse_intent_mapping() -> None:
    assert linear_state_to_inbound_intent(LinearWorkflowState.DONE) == InboundIntent.RESOLVE
    assert linear_state_to_inbound_intent(LinearWorkflowState.CANCELED) == InboundIntent.DROP
    assert (
        linear_state_to_inbound_intent(LinearWorkflowState.IN_PROGRESS)
        == InboundIntent.REOPEN_IN_PROGRESS
    )
    assert linear_state_to_inbound_intent(LinearWorkflowState.TRIAGE) == InboundIntent.NONE
    assert (
        linear_state_to_inbound_intent(LinearWorkflowState.AWAITING_CUSTOMER) == InboundIntent.NONE
    )


def test_priority_mapping_is_monotonic_into_linear_scale() -> None:
    assert ticket_priority_to_linear(Priority.P0) == 1  # urgent
    assert ticket_priority_to_linear(Priority.P1) == 2  # high
    assert ticket_priority_to_linear(Priority.P4) == 4  # low
    # Non-increasing urgency as the tier drops.
    tiers = (Priority.P0, Priority.P1, Priority.P2, Priority.P3, Priority.P4)
    seq = [ticket_priority_to_linear(p) for p in tiers]
    assert seq == sorted(seq)


def test_description_includes_orgs_links_and_display_id() -> None:
    t = _ticket(
        severity=Severity.BLOCKING,
        original_slack_link="https://x.slack.com/archives/C1/p123",
        prod_link="https://app.example.com/x",
    )
    body = build_issue_description(t, ["Acme Corp", "Globex"])
    assert "TIC-007" in body
    assert "Acme Corp, Globex" in body
    assert "Original thread" in body
    assert "Prod link" in body
    assert "Crashes on iOS 18." in body
