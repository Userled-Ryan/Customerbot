from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel

from customerbot.domain.tickets.value_objects import (
    ACVTier,
    ArticleStatus,
    CustomerWeight,
    Lane,
    Priority,
    RenewalStatus,
    Sentiment,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Ticket(BaseModel):
    """A v1 ticket. Mirrors flow doc §14 — stored authoritatively in SQL."""

    id: int | None = None
    title: str
    type: TicketType
    subtype: TicketSubtype
    status: TicketStatus = TicketStatus.NEW
    lane: Lane | None = None
    priority: Priority = Priority.P3
    severity: Severity = Severity.UNSURE
    feature: str | None = None
    description: str = ""
    reporter_user_id: str
    assigned_user_id: str | None = None
    source: Source
    original_slack_link: str | None = None
    prod_link: str | None = None
    screenshot_url: str | None = None
    replay_link: str | None = None
    affected_user: str | None = None
    blocking_impact: str | None = None
    deadline: date | None = None
    card_channel_id: str | None = None
    card_message_ts: str | None = None
    created_at: datetime = _utcnow()
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    updated_at: datetime = _utcnow()

    @property
    def display_id(self) -> str:
        return f"TIC-{self.id:03d}" if self.id is not None else "TIC-???"


class Org(BaseModel):
    """A customer org. v1: seeded manually via `/csbot org add` (legacy admin)."""

    id: str
    name: str
    slack_channel_id: str | None = None
    acv_tier: ACVTier | None = None
    sentiment: Sentiment | None = None
    renewal_date: date | None = None
    renewal_status: RenewalStatus | None = None
    csm_user_id: str | None = None
    created_at: datetime = _utcnow()
    updated_at: datetime = _utcnow()


class Article(BaseModel):
    id: int | None = None
    title: str
    status: ArticleStatus = ArticleStatus.SUGGESTED
    owner_user_id: str | None = None
    url: str | None = None
    created_at: datetime = _utcnow()
    published_at: datetime | None = None
    updated_at: datetime = _utcnow()


# --- Customer weight (flow §5b) ---
#
# Bucketing of (ACV × sentiment × renewal) into a discrete tier used as one
# axis of the prio matrix lookup. Numbers are calibrated heuristics — Chunk 7
# will re-read these alongside the prio matrix file, and §18 of the flow doc
# calls out "first-week calibration" as the time to revisit.

_ACV_WEIGHT: dict[ACVTier, float] = {
    ACVTier.SMALL: 1.0,
    ACVTier.MID: 2.0,
    ACVTier.LARGE: 3.0,
    ACVTier.ENTERPRISE: 4.0,
}

_SENTIMENT_MULTIPLIER: dict[Sentiment, float] = {
    Sentiment.NEGATIVE: 1.5,
    Sentiment.NEUTRAL: 1.0,
    Sentiment.POSITIVE: 0.8,
}

_RENEWAL_MULTIPLIER: dict[RenewalStatus, float] = {
    RenewalStatus.AT_RISK: 1.5,
    RenewalStatus.STABLE: 1.0,
    RenewalStatus.COMMITTED: 0.9,
    RenewalStatus.UNKNOWN: 1.0,
}


def customer_weight(
    acv: ACVTier | None,
    sentiment: Sentiment | None,
    renewal: RenewalStatus | None,
) -> CustomerWeight:
    """Compute the customer-weight tier for an org. Missing fields default neutral."""
    acv_score = _ACV_WEIGHT[acv] if acv is not None else _ACV_WEIGHT[ACVTier.SMALL]
    sentiment_mult = (
        _SENTIMENT_MULTIPLIER[sentiment]
        if sentiment is not None
        else _SENTIMENT_MULTIPLIER[Sentiment.NEUTRAL]
    )
    renewal_mult = (
        _RENEWAL_MULTIPLIER[renewal]
        if renewal is not None
        else _RENEWAL_MULTIPLIER[RenewalStatus.UNKNOWN]
    )
    score = acv_score * sentiment_mult * renewal_mult
    if score < 1.5:
        return CustomerWeight.LOW
    if score < 3.0:
        return CustomerWeight.MEDIUM
    if score < 5.0:
        return CustomerWeight.HIGH
    return CustomerWeight.CRITICAL
