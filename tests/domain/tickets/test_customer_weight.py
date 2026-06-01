from __future__ import annotations

from customerbot.domain.tickets.entities import customer_weight
from customerbot.domain.tickets.value_objects import (
    ACVTier,
    CustomerWeight,
    Priority,
    RenewalStatus,
    Sentiment,
    bump_one_tier,
)


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


def test_bump_one_tier() -> None:
    assert bump_one_tier(Priority.P4) == Priority.P3
    assert bump_one_tier(Priority.P3) == Priority.P2
    assert bump_one_tier(Priority.P2) == Priority.P1
    assert bump_one_tier(Priority.P1) == Priority.P0
    assert bump_one_tier(Priority.P0) == Priority.P0  # never above P0
