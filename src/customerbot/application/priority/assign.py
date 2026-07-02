"""Matrix-driven priority assignment on ticket creation (flow §7a)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from customerbot.application.priority.matrix import PriorityMatrix
from customerbot.domain.tickets.entities import (
    Org,
    Ticket,
    customer_weight,
)
from customerbot.domain.tickets.ports import EventLogRepositoryPort
from customerbot.domain.tickets.value_objects import (
    CustomerWeight,
    Priority,
    Severity,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AssignPriority:
    """Two-step:

    - `suggest(org, severity)` returns the matrix-driven priority. Pure.
    - `record_assignment(ticket)` writes the prio-change event row
      (`null → Pn`, reason `"matrix lookup"`). The SE adjusts priority from
      the ticket card's priority dropdown in the feed channel, so no override
      DM is sent.
    """

    def __init__(
        self,
        matrix: PriorityMatrix,
        events: EventLogRepositoryPort,
    ) -> None:
        self._matrix = matrix
        self._events = events

    def _weight_for(self, org: Org | None, today: date) -> CustomerWeight:
        if org is None:
            return CustomerWeight.LOW
        return customer_weight(
            org.acv_tier,
            org.sentiment,
            org.renewal_status,
            renewal_date=org.renewal_date,
            today=today,
        )

    def suggest(self, org: Org | None, severity: Severity) -> Priority:
        weight = self._weight_for(org, _utcnow().date())
        return self._matrix.lookup(weight, severity)

    async def record_assignment(self, ticket: Ticket) -> None:
        """Audit-only: log the matrix-assigned priority (`null → Pn`)."""
        if ticket.id is None:
            return
        await self._events.append_prio_change(
            ticket_id=ticket.id,
            from_priority=None,
            to_priority=ticket.priority,
            by_user_id=None,
            at=_utcnow(),
            reason="matrix lookup",
        )
