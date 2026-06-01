"""Block-Kit button payload codec for priority-change clicks.

All three priority-change flows (initial-assignment override, multi-customer
bump, P0 candidate) use the SAME action id with a JSON-encoded value carrying
the ticket id + target priority + reason. One handler routes all of them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from customerbot.domain.tickets.value_objects import Priority

ACTION_SET_PRIORITY = "set_ticket_priority"
ACTION_DISMISS_PRIO_DM = "dismiss_prio_dm"

# Reason codes — these land in the `reason` column of event_prio_changes.
REASON_MANUAL_OVERRIDE = "manual override"
REASON_MULTI_CUSTOMER_BUMP = "multi-customer bump"
REASON_P0_CANDIDATE = "P0 candidate confirmed"


@dataclass(frozen=True)
class PriorityChangePayload:
    ticket_id: int
    priority: Priority
    reason: str

    def encode(self) -> str:
        return json.dumps(
            {
                "ticket_id": self.ticket_id,
                "priority": self.priority.value,
                "reason": self.reason,
            }
        )

    @classmethod
    def decode(cls, value: str) -> PriorityChangePayload:
        data = json.loads(value)
        return cls(
            ticket_id=int(data["ticket_id"]),
            priority=Priority(data["priority"]),
            reason=str(data["reason"]),
        )
