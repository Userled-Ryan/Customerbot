"""Pure helpers: stage selection, threshold math, transition gating."""

from __future__ import annotations

from datetime import datetime, timedelta

from customerbot.application.sla.targets import (
    CLOCK_STAGES,
    applicable_stages,
    compute_state,
    evaluate_clock,
    is_paused,
    stage_reference_time,
    stage_target,
    target_for_priority,
    transition_should_dm,
)
from customerbot.config import SLATarget, _default_sla_targets
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute)


def _ticket(
    *,
    status: TicketStatus = TicketStatus.NEW,
    priority: Priority = Priority.P2,
    created_at: datetime | None = None,
    first_response_at: datetime | None = None,
) -> Ticket:
    return Ticket(
        title="t",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        priority=priority,
        reporter_user_id="U",
        source=Source.CUSTOMER_CHANNEL,
        created_at=created_at or _ts(2026, 6, 1, 9, 0),
        first_response_at=first_response_at,
    )


# --- compute_state ------------------------------------------------------------


def test_compute_state_green_below_half() -> None:
    assert compute_state(timedelta(minutes=15), timedelta(minutes=60)) == SLAState.GREEN


def test_compute_state_amber_at_half() -> None:
    assert compute_state(timedelta(minutes=30), timedelta(minutes=60)) == SLAState.AMBER


def test_compute_state_red_at_target() -> None:
    assert compute_state(timedelta(minutes=60), timedelta(minutes=60)) == SLAState.RED


def test_compute_state_red_past_target() -> None:
    assert compute_state(timedelta(minutes=120), timedelta(minutes=60)) == SLAState.RED


def test_compute_state_red_on_zero_target_defensive() -> None:
    assert compute_state(timedelta(minutes=1), timedelta(0)) == SLAState.RED


# --- applicable_stages --------------------------------------------------------


def test_new_ticket_runs_first_response_and_resolution() -> None:
    t = _ticket(status=TicketStatus.NEW)
    assert applicable_stages(t) == (SLAStage.FIRST_RESPONSE, SLAStage.RESOLUTION)


def test_in_progress_ticket_runs_status_update_and_resolution() -> None:
    t = _ticket(
        status=TicketStatus.IN_PROGRESS,
        first_response_at=_ts(2026, 6, 1, 10, 0),
    )
    assert applicable_stages(t) == (SLAStage.STATUS_UPDATE, SLAStage.RESOLUTION)


def test_awaiting_ticket_is_paused() -> None:
    t = _ticket(status=TicketStatus.AWAITING_CUSTOMER)
    assert is_paused(t)
    assert applicable_stages(t) == ()


def test_resolved_ticket_has_no_clocks() -> None:
    t = _ticket(status=TicketStatus.RESOLVED)
    assert applicable_stages(t) == ()


def test_closed_ticket_has_no_clocks() -> None:
    t = _ticket(status=TicketStatus.CLOSED)
    assert applicable_stages(t) == ()


def test_clock_stages_constant_excludes_nudge_stages() -> None:
    for stage in CLOCK_STAGES:
        assert stage in (
            SLAStage.FIRST_RESPONSE,
            SLAStage.STATUS_UPDATE,
            SLAStage.RESOLUTION,
        )


# --- stage_reference_time -----------------------------------------------------


def test_first_response_clock_starts_from_created_at() -> None:
    t = _ticket(created_at=_ts(2026, 6, 1, 9, 0))
    assert stage_reference_time(t, SLAStage.FIRST_RESPONSE) == _ts(2026, 6, 1, 9, 0)


def test_status_update_clock_needs_first_response_at() -> None:
    t = _ticket(first_response_at=None)
    assert stage_reference_time(t, SLAStage.STATUS_UPDATE) is None


def test_status_update_clock_starts_from_first_response_at() -> None:
    t = _ticket(first_response_at=_ts(2026, 6, 1, 10, 30))
    assert stage_reference_time(t, SLAStage.STATUS_UPDATE) == _ts(2026, 6, 1, 10, 30)


# --- stage_target -------------------------------------------------------------


def test_status_update_target_none_when_uncommitted() -> None:
    t = SLATarget(first_response_minutes=10, status_update_hours=None)
    assert stage_target(t, SLAStage.STATUS_UPDATE) is None


def test_resolution_target_none_when_uncommitted() -> None:
    t = SLATarget(first_response_minutes=10, resolution_hours=None)
    assert stage_target(t, SLAStage.RESOLUTION) is None


def test_first_response_target_uses_minutes() -> None:
    t = SLATarget(first_response_minutes=45)
    assert stage_target(t, SLAStage.FIRST_RESPONSE) == timedelta(minutes=45)


# --- evaluate_clock end-to-end ------------------------------------------------


def test_evaluate_clock_returns_state_per_window() -> None:
    target = SLATarget(first_response_minutes=60)
    t = _ticket(created_at=_ts(2026, 6, 1, 9, 0))
    # 20 min in → green
    assert (
        evaluate_clock(t, SLAStage.FIRST_RESPONSE, target, _ts(2026, 6, 1, 9, 20)) == SLAState.GREEN
    )
    # 40 min in → amber
    assert (
        evaluate_clock(t, SLAStage.FIRST_RESPONSE, target, _ts(2026, 6, 1, 9, 40)) == SLAState.AMBER
    )
    # 90 min in → red (breach)
    assert (
        evaluate_clock(t, SLAStage.FIRST_RESPONSE, target, _ts(2026, 6, 1, 10, 30)) == SLAState.RED
    )


def test_evaluate_clock_returns_none_for_missing_target() -> None:
    target = SLATarget(first_response_minutes=10, status_update_hours=None)
    t = _ticket(
        status=TicketStatus.IN_PROGRESS,
        first_response_at=_ts(2026, 6, 1, 10, 0),
    )
    assert evaluate_clock(t, SLAStage.STATUS_UPDATE, target, _ts(2026, 6, 1, 12, 0)) is None


def test_evaluate_clock_returns_none_when_reference_missing() -> None:
    target = SLATarget(first_response_minutes=10, status_update_hours=2)
    t = _ticket(first_response_at=None)
    assert evaluate_clock(t, SLAStage.STATUS_UPDATE, target, _ts(2026, 6, 1, 12, 0)) is None


# --- transition_should_dm -----------------------------------------------------


def test_dm_fires_on_green_to_amber() -> None:
    assert transition_should_dm(SLAState.GREEN, SLAState.AMBER) is True


def test_dm_fires_on_amber_to_red() -> None:
    assert transition_should_dm(SLAState.AMBER, SLAState.RED) is True


def test_dm_does_not_fire_on_same_state() -> None:
    assert transition_should_dm(SLAState.AMBER, SLAState.AMBER) is False
    assert transition_should_dm(SLAState.RED, SLAState.RED) is False


def test_dm_does_not_fire_for_green() -> None:
    assert transition_should_dm(None, SLAState.GREEN) is False
    assert transition_should_dm(SLAState.AMBER, SLAState.GREEN) is False


def test_dm_fires_first_observation_at_amber_or_red() -> None:
    # New ticket created already past 50% (e.g. backfilled / clock skew).
    assert transition_should_dm(None, SLAState.AMBER) is True
    assert transition_should_dm(None, SLAState.RED) is True


def test_dm_does_not_fire_on_recovery_red_to_amber() -> None:
    assert transition_should_dm(SLAState.RED, SLAState.AMBER) is False


# --- target_for_priority ------------------------------------------------------


def test_target_for_priority_picks_correct_row() -> None:
    targets = _default_sla_targets()
    p0 = target_for_priority(targets, Priority.P0)
    p4 = target_for_priority(targets, Priority.P4)
    assert p0 is not None and p4 is not None
    assert p0.first_response_minutes == 30
    assert p4.first_response_minutes == 48 * 60
