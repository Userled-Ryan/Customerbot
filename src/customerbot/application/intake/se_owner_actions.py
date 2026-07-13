"""Block-Kit payload codec for SE-owner-change clicks.

The ticket card's *SE owner* dropdown is a single `static_select` (one unique
`action_id`, so no `invalid_blocks` risk). Each option carries the target owner
+ ticket id JSON-encoded in its `value`; the handler decodes it and routes
through `ApplySeOwnerChange`, mirroring the `Set P-level` priority flow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

ACTION_SET_SE_OWNER = "set_ticket_se_owner"


@dataclass(frozen=True)
class SeOwnerChangePayload:
    ticket_id: int
    owner_user_id: str

    def encode(self) -> str:
        return json.dumps({"ticket_id": self.ticket_id, "owner_user_id": self.owner_user_id})

    @classmethod
    def decode(cls, value: str) -> SeOwnerChangePayload:
        data = json.loads(value)
        return cls(
            ticket_id=int(data["ticket_id"]),
            owner_user_id=str(data["owner_user_id"]),
        )
