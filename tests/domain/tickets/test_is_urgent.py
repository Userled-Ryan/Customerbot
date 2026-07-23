from __future__ import annotations

import pytest

from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _ticket(*, urgent: bool, status: TicketStatus, lane: Lane | None = Lane.SE_ACTION) -> Ticket:
    return Ticket(
        title="t",
        type=TicketType.BUG,
        subtype=TicketSubtype.CUSTOMER_SPECIFIC,
        status=status,
        lane=lane,
        reporter_user_id="U",
        source=Source.DM,
        urgent=urgent,
    )


def test_is_urgent_only_true_for_flagged_and_new() -> None:
    assert _ticket(urgent=True, status=TicketStatus.NEW).is_urgent is True


@pytest.mark.parametrize(
    "status",
    [
        TicketStatus.IN_PROGRESS,
        TicketStatus.AWAITING_CUSTOMER,
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    ],
)
def test_is_urgent_false_once_ticket_leaves_new(status: TicketStatus) -> None:
    # Moving on ends effective urgency without clearing the stored flag.
    ticket = _ticket(urgent=True, status=status)
    assert ticket.urgent is True
    assert ticket.is_urgent is False


def test_is_urgent_false_when_not_flagged() -> None:
    assert _ticket(urgent=False, status=TicketStatus.NEW).is_urgent is False


def test_is_urgent_false_once_handed_to_dev_lane() -> None:
    # Dev is now on it — urgency (and the SE nag) ends even though it's still NEW.
    ticket = _ticket(urgent=True, status=TicketStatus.NEW, lane=Lane.DEV_ACTION)
    assert ticket.urgent is True
    assert ticket.is_urgent is False
