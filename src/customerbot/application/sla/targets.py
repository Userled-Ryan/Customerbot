"""Pure SLA-target helpers — no I/O, no DB, no Slack.

Three SLA clocks, one per stage (flow §5d):

- **FIRST_RESPONSE** runs from `created_at` until `first_response_at` is set
  (which fires on the first New → In progress transition, per ambiguity #8).
- **STATUS_UPDATE** runs from `first_response_at` while the ticket is
  In progress. In v1 it's a cumulative "time since work began" clock
  rather than a cadence; resetting on each posted update is deferred until
  Chunk 11's comms-drafts land. Documented limitation; revisit per flow §18.
- **RESOLUTION** runs from `created_at` until the ticket leaves the live
  workflow (Awaiting customer / Closed).

Each clock independently transitions GREEN → AMBER → RED:
  - GREEN  : elapsed < 50% of target
  - AMBER  : 50% ≤ elapsed < 100% of target
  - RED    : elapsed ≥ 100% (target breached)

Tickets in status Awaiting customer confirmation are *paused* — the scan
skips them entirely. The naive `elapsed = now − reference` does not subtract
prior pause periods (v1 simplification); a ticket that's been awaiting
beyond its target may flip to RED on resume. Acceptable for v1; flow §18
calls this out as a first-week calibration item.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import Priority, SLATarget, TicketStatus

AMBER_THRESHOLD = 0.5

# The three "clock" stages — the awaiting-nudge stages aren't clocks.
CLOCK_STAGES: tuple[SLAStage, ...] = (
    SLAStage.FIRST_RESPONSE,
    SLAStage.STATUS_UPDATE,
    SLAStage.RESOLUTION,
)


def is_paused(ticket: Ticket) -> bool:
    """True when SLA scans should skip the ticket (status = Awaiting customer)."""
    return ticket.status == TicketStatus.AWAITING_CUSTOMER


def applicable_stages(ticket: Ticket) -> tuple[SLAStage, ...]:
    """Which SLA clocks are running for this ticket right now.

    Returns an empty tuple if no clocks apply (paused, resolved, closed).
    """
    if is_paused(ticket):
        return ()
    if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        return ()
    stages: list[SLAStage] = []
    if ticket.first_response_at is None:
        stages.append(SLAStage.FIRST_RESPONSE)
    else:
        # Once first response has fired, the status-update and resolution
        # clocks are both running through to the end of the live window.
        stages.append(SLAStage.STATUS_UPDATE)
    stages.append(SLAStage.RESOLUTION)
    return tuple(stages)


def stage_reference_time(ticket: Ticket, stage: SLAStage) -> datetime | None:
    """The instant from which to measure elapsed for this stage, or None if N/A."""
    if stage == SLAStage.FIRST_RESPONSE:
        return ticket.created_at
    if stage == SLAStage.STATUS_UPDATE:
        return ticket.first_response_at
    if stage == SLAStage.RESOLUTION:
        return ticket.created_at
    return None


def stage_target(target: SLATarget, stage: SLAStage) -> timedelta | None:
    """The configured target window for this stage, or None when uncommitted."""
    if stage == SLAStage.FIRST_RESPONSE:
        return timedelta(minutes=target.first_response_minutes)
    if stage == SLAStage.STATUS_UPDATE:
        return (
            timedelta(hours=target.status_update_hours)
            if target.status_update_hours is not None
            else None
        )
    if stage == SLAStage.RESOLUTION:
        return (
            timedelta(hours=target.resolution_hours)
            if target.resolution_hours is not None
            else None
        )
    return None


def compute_state(elapsed: timedelta, target: timedelta) -> SLAState:
    """Map elapsed/target ratio onto GREEN/AMBER/RED."""
    if target <= timedelta(0):
        return SLAState.RED
    ratio = elapsed / target
    if ratio < AMBER_THRESHOLD:
        return SLAState.GREEN
    if ratio < 1.0:
        return SLAState.AMBER
    return SLAState.RED


def evaluate_clock(
    ticket: Ticket,
    stage: SLAStage,
    target: SLATarget,
    now: datetime,
) -> SLAState | None:
    """Compute the current SLA state for one (ticket, stage) clock.

    Returns None when this stage has no target (uncommitted), is N/A for the
    ticket's current status, or the reference time is missing.
    """
    ref = stage_reference_time(ticket, stage)
    if ref is None:
        return None
    window = stage_target(target, stage)
    if window is None:
        return None
    elapsed = now - ref
    if elapsed < timedelta(0):
        return SLAState.GREEN
    return compute_state(elapsed, window)


def transition_should_dm(previous: SLAState | None, current: SLAState) -> bool:
    """Whether a transition GREEN → AMBER or AMBER → RED warrants a DM.

    Plan §Chunk 8: "DM SE on green→amber and amber→red transitions only."
    First observation of a ticket already at AMBER (no prior row) also fires;
    we treat "no prior row" as the previous state being one step lower so a
    brand-new ticket created already over-budget still alerts.
    """
    if current == SLAState.GREEN:
        return False
    if previous == current:
        return False
    # Recovery (RED → AMBER) shouldn't happen with naive elapsed, but be defensive.
    return not (previous == SLAState.RED and current == SLAState.AMBER)


def target_for_priority(targets: dict[str, SLATarget], priority: Priority) -> SLATarget | None:
    """Look up the SLA target row for a ticket's priority tier."""
    return targets.get(priority.value)
