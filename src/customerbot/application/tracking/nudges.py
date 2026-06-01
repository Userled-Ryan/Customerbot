"""Customer-comms nudge jobs (min-spec §9b + §9d, plan Chunk 11).

Two scheduled jobs live here, sharing the comms-drafts library:

- `ConfirmationNudgeJob` — for tickets in `Awaiting customer confirmation`,
  DM SE a §9d nudge draft at 24h, 72h, and 7d after they entered awaiting.
  Throttled once per checkpoint via `sla_dm_state` under the
  `SE_NUDGE_24H / 72H / 7D` stages. Distinct from Chunk 8's CSM pre-close
  nudges, which DM CSMs at the same elapsed times but with the FYI-side
  blocks (`csm_pre_close_blocks`).

- `StatusUpdateCadenceJob` — for live in-progress tickets, DM SE a §9b
  status-update draft on the cadence implied by the ticket's SLA tier
  (`SLATarget.status_update_hours`). Last fire is recorded via the
  `STATUS_UPDATE_DRAFT` stage in `sla_dm_state` (its `last_dm_at` is the
  bookmark); the next fire is `status_update_hours` after that. Tier
  has no committed cadence (e.g. P4 default) → skip silently.

Both jobs always DM SE — they're the customer-comms drafts SE chooses
when (or whether) to relay. The bot never sends to customers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from customerbot.application.tracking.comms_drafts import (
    Draft,
    auto_close_date,
    is_status_update_due,
    nudge_for_confirmation,
    status_update,
)
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.bot_state.ports import SLADMStateRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import EventLogRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import SLATarget, TicketStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- §9d confirmation nudges --------------------------------------------------


# (stage, elapsed-hours-since-awaiting). The plan's three checkpoints.
_NUDGE_SCHEDULE: tuple[tuple[SLAStage, timedelta], ...] = (
    (SLAStage.SE_NUDGE_24H, timedelta(hours=24)),
    (SLAStage.SE_NUDGE_72H, timedelta(hours=72)),
    (SLAStage.SE_NUDGE_7D, timedelta(days=7)),
)


@dataclass
class NudgeOutcome:
    fired: list[tuple[int, SLAStage]]


class ConfirmationNudgeJob:
    """Daily job — DMs SE the §9d nudge draft at 24h / 72h / 7d."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        sla_state: SLADMStateRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        auto_close_days: int = 7,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._sla_state = sla_state
        self._slack = slack
        self._se_user_id = se_user_id
        self._auto_close_days = auto_close_days

    async def execute(self, *, now: datetime | None = None) -> NudgeOutcome:
        when = now or _utcnow()
        fired: list[tuple[int, SLAStage]] = []
        live = await self._tickets.query_live()
        for ticket in live:
            if ticket.id is None:
                continue
            if ticket.status != TicketStatus.AWAITING_CUSTOMER:
                continue
            entered_at = await self._events.last_status_change_into(
                ticket.id, TicketStatus.AWAITING_CUSTOMER
            )
            if entered_at is None:
                logger.warning(
                    "Ticket %d in awaiting with no transition event; skipping nudge",
                    ticket.id,
                )
                continue
            elapsed = when - entered_at
            close_date = auto_close_date(entered_at, auto_close_days=self._auto_close_days)
            for stage, threshold in _NUDGE_SCHEDULE:
                if elapsed < threshold:
                    continue
                already_sent = await self._sla_state.get(ticket.id, stage)
                if already_sent is not None:
                    continue
                draft = nudge_for_confirmation(ticket, auto_close_at=close_date)
                await self._dm_draft(ticket, draft, stage=stage)
                await self._sla_state.upsert(ticket.id, stage, SLAState.RED, when, now=when)
                fired.append((ticket.id, stage))
        return NudgeOutcome(fired=fired)

    async def _dm_draft(self, ticket: Ticket, draft: Draft, *, stage: SLAStage) -> None:
        blocks = _draft_dm_blocks(draft, stage_hint=_NUDGE_LABELS[stage])
        await self._slack.send_dm_blocks(
            self._se_user_id,
            blocks,
            text=f"Nudge draft: {ticket.display_id}",
        )

    async def run_loop(self, interval_seconds: int = 86400) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Confirmation-nudge loop error")
            await asyncio.sleep(interval_seconds)


_NUDGE_LABELS: dict[SLAStage, str] = {
    SLAStage.SE_NUDGE_24H: "24h after entering awaiting",
    SLAStage.SE_NUDGE_72H: "72h after entering awaiting",
    SLAStage.SE_NUDGE_7D: "7d after entering awaiting (auto-close imminent)",
}


# --- §9b status-update cadence -----------------------------------------------


@dataclass
class StatusUpdateOutcome:
    fired: list[int]  # ticket ids that fired this run


class StatusUpdateCadenceJob:
    """Hourly job — DMs SE a §9b status-update draft per SLA tier cadence."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        sla_state: SLADMStateRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        sla_targets: dict[str, SLATarget],
    ) -> None:
        self._tickets = tickets
        self._sla_state = sla_state
        self._slack = slack
        self._se_user_id = se_user_id
        self._sla_targets = sla_targets

    async def execute(self, *, now: datetime | None = None) -> StatusUpdateOutcome:
        when = now or _utcnow()
        fired: list[int] = []
        live = await self._tickets.query_live()
        for ticket in live:
            if ticket.id is None:
                continue
            if ticket.status != TicketStatus.IN_PROGRESS:
                continue
            if ticket.first_response_at is None:
                # Status-update clock only starts once SE has acked.
                continue
            target = self._sla_targets.get(ticket.priority.value)
            if target is None or target.status_update_hours is None:
                continue
            prior = await self._sla_state.get(ticket.id, SLAStage.STATUS_UPDATE_DRAFT)
            last_drafted_at = prior.last_dm_at if prior else None
            if not is_status_update_due(
                ticket,
                target_status_update_hours=target.status_update_hours,
                last_drafted_at=last_drafted_at,
                now=when,
            ):
                continue
            next_checkpoint = when + timedelta(hours=target.status_update_hours)
            draft = status_update(
                ticket,
                latest_internal_note=None,
                next_checkpoint=next_checkpoint,
            )
            blocks = _draft_dm_blocks(
                draft,
                stage_hint=f"every {target.status_update_hours}h "
                f"per {ticket.priority.value} SLA cadence",
            )
            await self._slack.send_dm_blocks(
                self._se_user_id,
                blocks,
                text=f"Status-update draft: {ticket.display_id}",
            )
            await self._sla_state.upsert(
                ticket.id,
                SLAStage.STATUS_UPDATE_DRAFT,
                SLAState.GREEN,
                when,
                now=when,
            )
            fired.append(ticket.id)
        return StatusUpdateOutcome(fired=fired)

    async def run_loop(self, interval_seconds: int = 3600) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Status-update cadence loop error")
            await asyncio.sleep(interval_seconds)


# --- Rendering helper (shared) ------------------------------------------------


def _draft_dm_blocks(draft: Draft, *, stage_hint: str) -> list[dict[str, Any]]:
    """Wrap the draft's body in a DM that adds a stage hint as a context line.

    The §9d / §9b drafts both surface to SE as "here's what to send; here's
    when it fires next". The stage hint tells SE which checkpoint triggered
    this so they can tune cadence/copy from real usage.
    """
    blocks = draft.blocks()
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_Cadence: {stage_hint}._"}],
        }
    )
    return blocks
