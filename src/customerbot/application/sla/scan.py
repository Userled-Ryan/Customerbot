"""SLA state-machine scan (flow §5d, plan Chunk 8).

Every 15 minutes, walk every live ticket × every applicable SLA stage,
recompute GREEN/AMBER/RED, persist the new state in `sla_dm_state`, and
DM SE on GREEN→AMBER and AMBER→RED transitions only — never on
unchanged state or recoveries.

Pauses: tickets in `Awaiting customer confirmation` are skipped entirely;
their `sla_dm_state` rows are preserved untouched (see `targets.is_paused`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from customerbot.application.sla import messages
from customerbot.application.sla.targets import (
    applicable_stages,
    evaluate_clock,
    stage_reference_time,
    stage_target,
    target_for_priority,
    transition_should_dm,
)
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.bot_state.ports import SLADMStateRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import SLATarget

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SLAStateMachine:
    """Scheduled job: re-evaluate every live ticket's SLA clocks."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        sla_state: SLADMStateRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        sla_targets: dict[str, SLATarget],
        workspace_url: str,
    ) -> None:
        self._tickets = tickets
        self._sla_state = sla_state
        self._slack = slack
        self._se_user_id = se_user_id
        self._sla_targets = sla_targets
        self._workspace_url = workspace_url

    async def execute(self, *, now: datetime | None = None) -> list[tuple[int, SLAStage, SLAState]]:
        """Return the (ticket_id, stage, new_state) tuples that fired a DM this run."""
        when = now or _utcnow()
        fired: list[tuple[int, SLAStage, SLAState]] = []
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
                last_dm_at = prior.last_dm_at if prior else None
                if transition_should_dm(prior_state, new_state):
                    blocks = self._blocks_for(ticket, stage, new_state, when, target)
                    await self._slack.send_dm_blocks(
                        self._se_user_id,
                        blocks,
                        text=f"SLA {new_state.value.upper()}: {ticket.display_id}",
                    )
                    last_dm_at = when
                    fired.append((ticket.id, stage, new_state))
                await self._sla_state.upsert(
                    ticket.id,
                    stage,
                    new_state,
                    last_dm_at,
                    now=when,
                )
        return fired

    def _blocks_for(
        self,
        ticket: Ticket,
        stage: SLAStage,
        new_state: SLAState,
        now: datetime,
        target: SLATarget,
    ) -> list[dict[str, Any]]:
        ref = stage_reference_time(ticket, stage)
        window = stage_target(target, stage)
        # Both have to be non-None — evaluate_clock would have returned None otherwise.
        assert ref is not None
        assert window is not None
        elapsed = now - ref
        return messages.sla_transition_blocks(
            ticket, stage, new_state, elapsed, window, workspace_url=self._workspace_url
        )

    async def run_loop(self, interval_seconds: int = 900) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("SLA scan loop error")
            await asyncio.sleep(interval_seconds)
