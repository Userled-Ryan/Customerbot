"""Block-Kit button payload codec for priority-change clicks.

All three priority-change flows (initial-assignment override, multi-customer
bump, P0 candidate) use the SAME action id with a JSON-encoded value carrying
the ticket id + target priority + reason. One handler routes all of them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from customerbot.domain.tickets.value_objects import Priority

ACTION_SET_PRIORITY = "set_ticket_priority"
ACTION_DISMISS_PRIO_DM = "dismiss_prio_dm"

# Slack requires every interactive element in a single message to carry a
# *unique* `action_id` — the initial-assignment override DM shows four buttons
# (P1..P4) at once, so they can't all be the bare `ACTION_SET_PRIORITY` or
# Slack rejects the whole message with `invalid_blocks`. Per-tier ids keep them
# distinct; the single-button bump / P0 flows still use the bare id. One handler
# routes them all via `ACTION_SET_PRIORITY_PATTERN` (the target priority + reason
# travel in the button `value`, not the action_id).
ACTION_SET_PRIORITY_PATTERN = re.compile(rf"^{re.escape(ACTION_SET_PRIORITY)}(?::|$)")


def set_priority_action_id(priority: Priority) -> str:
    """Per-tier `action_id` for a set-priority button (e.g. `set_ticket_priority:P1`)."""
    return f"{ACTION_SET_PRIORITY}:{priority.value}"


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
