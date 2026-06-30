"""Apply a priority change confirmed by SE clicking a button.

Wired to `ACTION_SET_PRIORITY`. Handles every flow that produces a set-priority
control: the card's `Set P-level` select, the initial-assignment override DM,
multi-customer bump confirmation, and P0 candidate confirmation.

After applying the change this refreshes the ticket card and pushes the new
priority onto the Linear mirror, so a priority set from anywhere updates
everywhere (the in-channel card + Linear).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.application.priority.actions import PriorityChangePayload
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import Priority

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ApplyPriorityChange:
    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        slack: SlackPort | None = None,
        orgs: OrgRepositoryPort | None = None,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._slack = slack
        self._orgs = orgs
        self._linear = linear

    async def execute(self, payload: PriorityChangePayload, *, by_user_id: str) -> Priority | None:
        existing = await self._tickets.get(payload.ticket_id)
        if existing is None or existing.id is None:
            logger.warning("Priority change clicked on missing ticket %s", payload.ticket_id)
            return None
        if existing.priority == payload.priority:
            # No-op click (SE pressed the already-active tier). Still log it.
            logger.info(
                "Priority click on %s requested %s but already %s — no change",
                existing.display_id,
                payload.priority.value,
                existing.priority.value,
            )
            return existing.priority
        now = _utcnow()
        await self._tickets.update_priority(existing.id, payload.priority, now=now)
        await self._events.append_prio_change(
            ticket_id=existing.id,
            from_priority=existing.priority,
            to_priority=payload.priority,
            by_user_id=by_user_id,
            at=now,
            reason=payload.reason,
        )
        # Reflect the change everywhere: the in-channel card and the Linear mirror.
        if self._slack is not None and self._orgs is not None:
            await refresh_card(self._slack, self._tickets, self._orgs, existing.id)
        if self._linear is not None:
            await self._linear.sync_priority(existing.id)
        return payload.priority
