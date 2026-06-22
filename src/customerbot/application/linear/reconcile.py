"""ReconcileLinear — the durable "no desync" backstop (v1.5, Chunk D).

Because the hot-path Linear calls are best-effort (a Linear outage must never
break the Slack flow), an outbound write can occasionally be dropped, and a
single inbound webhook can be missed. This periodic sweep repairs both:

- **Outbound gap:** a live ticket with no `linear_issue_id` → create its mirror
  (and open it for dev if it's on the Dev lane).
- **Inbound gap:** a dev-lane ticket whose Linear state has moved on (Done /
  Canceled / Started) but isn't reflected on our side → apply it by replaying
  the *same* `LinearInboundHandler` path used by the webhook. That path is
  idempotent and notification-guarded, so a ticket already in sync is a no-op.

It deliberately does NOT push SE-lane state drift back to Linear — those are
silently closed by us and not worth fighting over; and it never overwrites a
dev's Linear state for dev-lane tickets (Linear is where they work).
"""

from __future__ import annotations

import asyncio
import logging

from customerbot.application.linear.inbound import LinearInboundEvent, LinearInboundHandler
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.linear.ports import LinearPort
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import Lane

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 600  # 10 min


class ReconcileLinear:
    def __init__(
        self,
        *,
        tickets: TicketRepositoryPort,
        linear: LinearPort,
        sync: LinearSync,
        inbound: LinearInboundHandler,
    ) -> None:
        self._tickets = tickets
        self._linear = linear
        self._sync = sync
        self._inbound = inbound

    async def execute(self) -> int:
        """Run one reconcile pass. Returns the number of tickets repaired."""
        repaired = 0
        live = await self._tickets.query_live()
        for ticket in live:
            if ticket.id is None:
                continue
            try:
                if ticket.linear_issue_id is None:
                    # Outbound gap — create the missing mirror.
                    await self._sync.mirror_new_ticket(ticket)
                    if ticket.lane == Lane.DEV_ACTION:
                        await self._sync.ensure_open_for_dev(ticket.id)
                    repaired += 1
                    continue

                if ticket.lane != Lane.DEV_ACTION:
                    continue  # don't fight SE-lane state; webhook handles dev side

                # Inbound gap — pull the current Linear state and replay it
                # through the (idempotent) inbound handler.
                state = await self._linear.get_issue_state(issue_id=ticket.linear_issue_id)
                if state is None:
                    continue
                event = LinearInboundEvent(
                    entity_type="Issue",
                    actor_id=None,
                    actor_name="reconcile",
                    issue_id=ticket.linear_issue_id,
                    new_state=state,
                )
                await self._inbound.handle(ticket, event)
            except Exception:
                logger.exception("Reconcile failed for ticket %s", ticket.id)
        return repaired

    async def run_loop(self, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Linear reconcile loop error")
            await asyncio.sleep(interval_seconds)
