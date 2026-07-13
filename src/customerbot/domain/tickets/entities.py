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
    ResolutionType,
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
    # SE owner (migration 0016) — the SE responsible for the ticket. Defaults to
    # the configured SE on creation (not exposed to the logger); reassigned from
    # the card's SE-owner dropdown and mirrored to Linear as the issue assignee.
    se_owner_user_id: str | None = None
    source: Source
    original_slack_link: str | None = None
    prod_link: str | None = None
    screenshot_url: str | None = None
    replay_link: str | None = None
    affected_user: str | None = None
    blocking_impact: str | None = None
    deadline: date | None = None
    # Resolution reporting (plan Part 2) — set when the SE marks the ticket
    # Resolved via the resolve modal. `resolution_pr_link` is only meaningful
    # when `resolution_type == CODE_CHANGE`.
    resolution_type: ResolutionType | None = None
    resolution_pr_link: str | None = None
    card_channel_id: str | None = None
    card_message_ts: str | None = None
    # SE-set flag: this ticket is waiting on a reply. Surfaced on the card and
    # in the daily 5pm reply-needed digest; cleared by the SE once they reply.
    reply_needed: bool = False
    # Linear mirror (v1.5) — populated once the ticket has a Linear issue.
    linear_issue_id: str | None = None
    linear_issue_identifier: str | None = None
    linear_issue_url: str | None = None
    created_at: datetime = _utcnow()
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    updated_at: datetime = _utcnow()

    @property
    def display_id(self) -> str:
        return f"TIC-{self.id:03d}" if self.id is not None else "TIC-???"


class Org(BaseModel):
    """A customer org. Seeded via scripts/seed_org.py (or import_orgs.py for bulk)."""

    id: str
    name: str
    slack_channel_id: str | None = None
    teams_channel_id: str | None = None
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


# Renewal-proximity multipliers. A contract nearing renewal raises the
# customer's weight so their tickets float up — two step-ups, at the 6-month
# and again at the 3-month mark. Mirrors the at-risk renewal-status multiplier
# at its peak (≤3mo ⇒ 1.5), with an intermediate step (≤6mo ⇒ 1.25).
RENEWAL_3MO_DAYS = 90
RENEWAL_6MO_DAYS = 182
_RENEWAL_PROXIMITY_3MO = 1.5  # ≤ 3 months out (or already overdue)
_RENEWAL_PROXIMITY_6MO = 1.25  # ≤ 6 months out
_RENEWAL_PROXIMITY_FAR = 1.0  # > 6 months out


def renewal_proximity_multiplier(renewal_date: date | None, today: date) -> float | None:
    """Weight multiplier from how close the contract renewal is, or None when
    there's no renewal date to measure against (caller falls back to status)."""
    if renewal_date is None:
        return None
    days_until = (renewal_date - today).days
    if days_until <= RENEWAL_3MO_DAYS:
        return _RENEWAL_PROXIMITY_3MO
    if days_until <= RENEWAL_6MO_DAYS:
        return _RENEWAL_PROXIMITY_6MO
    return _RENEWAL_PROXIMITY_FAR


def customer_weight(
    acv: ACVTier | None,
    sentiment: Sentiment | None,
    renewal: RenewalStatus | None = None,
    *,
    renewal_date: date | None = None,
    today: date | None = None,
) -> CustomerWeight:
    """Compute the customer-weight tier for an org. Missing fields default neutral.

    Renewal contributes via the *date* when one is set — proximity to renewal
    bumps the weight at the 6-month and 3-month marks. With no date it falls
    back to the static `renewal` status. `today` anchors the date math; the
    app passes the current date, and it defaults to `date.today()` for ad-hoc
    callers (scripts, previews)."""
    acv_score = _ACV_WEIGHT[acv] if acv is not None else _ACV_WEIGHT[ACVTier.SMALL]
    sentiment_mult = (
        _SENTIMENT_MULTIPLIER[sentiment]
        if sentiment is not None
        else _SENTIMENT_MULTIPLIER[Sentiment.NEUTRAL]
    )
    proximity_mult = renewal_proximity_multiplier(renewal_date, today or date.today())
    if proximity_mult is not None:
        renewal_mult = proximity_mult
    elif renewal is not None:
        renewal_mult = _RENEWAL_MULTIPLIER[renewal]
    else:
        renewal_mult = _RENEWAL_MULTIPLIER[RenewalStatus.UNKNOWN]
    score = acv_score * sentiment_mult * renewal_mult
    if score < 1.5:
        return CustomerWeight.LOW
    if score < 3.0:
        return CustomerWeight.MEDIUM
    if score < 5.0:
        return CustomerWeight.HIGH
    return CustomerWeight.CRITICAL
