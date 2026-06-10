from __future__ import annotations

from datetime import date, timedelta

from customerbot.domain.tickets.entities import (
    customer_weight,
    renewal_proximity_multiplier,
)
from customerbot.domain.tickets.value_objects import (
    ACVTier,
    CustomerWeight,
    Priority,
    RenewalStatus,
    Sentiment,
    bump_one_tier,
)

_TODAY = date(2026, 6, 10)


def test_small_neutral_stable_is_low() -> None:
    assert (
        customer_weight(ACVTier.SMALL, Sentiment.NEUTRAL, RenewalStatus.STABLE)
        == CustomerWeight.LOW
    )


def test_enterprise_negative_atrisk_is_critical() -> None:
    assert (
        customer_weight(ACVTier.ENTERPRISE, Sentiment.NEGATIVE, RenewalStatus.AT_RISK)
        == CustomerWeight.CRITICAL
    )


def test_missing_inputs_default_neutral() -> None:
    """An org with no ACV/sentiment/renewal info is treated as small/neutral/unknown."""
    assert customer_weight(None, None, None) == CustomerWeight.LOW


def test_positive_sentiment_pulls_score_down() -> None:
    pos = customer_weight(ACVTier.LARGE, Sentiment.POSITIVE, RenewalStatus.COMMITTED)
    neg = customer_weight(ACVTier.LARGE, Sentiment.NEGATIVE, RenewalStatus.AT_RISK)
    order = [
        CustomerWeight.LOW,
        CustomerWeight.MEDIUM,
        CustomerWeight.HIGH,
        CustomerWeight.CRITICAL,
    ]
    assert order.index(neg) > order.index(pos)


def test_renewal_proximity_multiplier_steps_up_at_6mo_then_3mo() -> None:
    far = renewal_proximity_multiplier(_TODAY + timedelta(days=300), _TODAY)
    six = renewal_proximity_multiplier(_TODAY + timedelta(days=150), _TODAY)  # ~5mo
    three = renewal_proximity_multiplier(_TODAY + timedelta(days=60), _TODAY)  # ~2mo
    overdue = renewal_proximity_multiplier(_TODAY - timedelta(days=10), _TODAY)
    assert far == 1.0
    assert six == 1.25
    assert three == 1.5
    assert overdue == 1.5  # past-due renewal is at least as urgent as ≤3mo
    assert renewal_proximity_multiplier(None, _TODAY) is None


def test_renewal_date_drives_weight_as_renewal_approaches() -> None:
    # Same org; only the renewal proximity changes. Weight must not decrease as
    # the renewal nears.
    far = customer_weight(
        ACVTier.MID, Sentiment.NEUTRAL, renewal_date=_TODAY + timedelta(days=300), today=_TODAY
    )
    near_6mo = customer_weight(
        ACVTier.MID, Sentiment.NEUTRAL, renewal_date=_TODAY + timedelta(days=150), today=_TODAY
    )
    near_3mo = customer_weight(
        ACVTier.MID, Sentiment.NEUTRAL, renewal_date=_TODAY + timedelta(days=60), today=_TODAY
    )
    order = [
        CustomerWeight.LOW,
        CustomerWeight.MEDIUM,
        CustomerWeight.HIGH,
        CustomerWeight.CRITICAL,
    ]
    assert order.index(near_6mo) >= order.index(far)
    assert order.index(near_3mo) >= order.index(near_6mo)
    # MID (2.0) × neutral (1.0): far=2.0 MEDIUM, 6mo=2.5 MEDIUM, 3mo=3.0 HIGH.
    assert far == CustomerWeight.MEDIUM
    assert near_3mo == CustomerWeight.HIGH


def test_renewal_date_takes_precedence_over_status() -> None:
    # A far-off date (×1.0) should override a stale at-risk status (×1.5).
    weight = customer_weight(
        ACVTier.MID,
        Sentiment.NEUTRAL,
        RenewalStatus.AT_RISK,
        renewal_date=_TODAY + timedelta(days=300),
        today=_TODAY,
    )
    assert weight == CustomerWeight.MEDIUM  # 2.0×1.0×1.0, not bumped by at-risk


def test_bump_one_tier() -> None:
    assert bump_one_tier(Priority.P4) == Priority.P3
    assert bump_one_tier(Priority.P3) == Priority.P2
    assert bump_one_tier(Priority.P2) == Priority.P1
    assert bump_one_tier(Priority.P1) == Priority.P0
    assert bump_one_tier(Priority.P0) == Priority.P0  # never above P0
