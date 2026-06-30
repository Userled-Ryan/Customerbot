"""SLA state-machine scan (flow §5d, plan Chunk 8).

Every 15 minutes, walk every live ticket × every applicable SLA stage,
recompute GREEN/AMBER/RED, and persist the new state in `sla_dm_state`.

**Silent by design (SE's call).** The per-transition amber/red SE DMs were
removed — the SE gets one consolidated open-tickets digest at 10:00 and 17:00
(`OpenTicketsDigestJob`) instead of escalation pings throughout the day. The
clocks still tick and persist so the state is available for future reporting;
they just no longer notify. `execute` still returns the amber/red transitions
it observed this run (no I/O), which is what the tests assert on.

Pauses: tickets in `Awaiting customer confirmation` are skipped entirely;
their `sla_dm_state` rows are preserved untouched (see `targets.is_paused`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from customerbot.application.sla.targets import (
    applicable_stages,
    evaluate_clock,
    target_for_priority,
    transition_should_dm,
)
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.bot_state.ports import SLADMStateRepositoryPort
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import SLATarget

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SLAStateMachine:
    """Scheduled job: re-evaluate every live ticket's SLA clocks (silently)."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        sla_state: SLADMStateRepositoryPort,
        sla_targets: dict[str, SLATarget],
    ) -> None:
        self._tickets = tickets
        self._sla_state = sla_state
        self._sla_targets = sla_targets

    async def execute(self, *, now: datetime | None = None) -> list[tuple[int, SLAStage, SLAState]]:
        """Persist clock states; return the amber/red transitions observed this run.

        No DMs are sent — the open-tickets digest is the sole SE notification.
        """
        when = now or _utcnow()
        escalations: list[tuple[int, SLAStage, SLAState]] = []
        live = await self._tickets.query_live()
        for ticket in live:
            if ticket.id is None:
                continue
            target = target_for_priority(self._sla_targets, ticket.priority)
            if target is None:
                continue
            for stage in applicable_stages(ticket):
                new_state = evaluate_clock(ticket, stage, target, when)
                if new_state is None:
                    continue
                prior = await self._sla_state.get(ticket.id, stage)
                prior_state = prior.last_state if prior else None
                if prior_state == new_state:
                    # Same state, nothing to do — don't even bump updated_at.
                    continue
                # `last_dm_at` is frozen now we no longer DM; carry it forward.
                last_dm_at = prior.last_dm_at if prior else None
                if transition_should_dm(prior_state, new_state):
                    escalations.append((ticket.id, stage, new_state))
                await self._sla_state.upsert(
                    ticket.id,
                    stage,
                    new_state,
                    last_dm_at,
                    now=when,
                )
        return escalations

    async def run_loop(self, interval_seconds: int = 900) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("SLA scan loop error")
            await asyncio.sleep(interval_seconds)
