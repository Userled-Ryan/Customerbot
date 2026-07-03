from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class TicketType(StrEnum):
    BUG = "bug"
    CONFIG = "config"
    FAQ = "faq"
    FEATURE_REQUEST = "feature-request"


class TicketSubtype(StrEnum):
    # Bug subtypes (flow §2)
    PLATFORM_WIDE = "platform-wide"
    CUSTOMER_SPECIFIC = "customer-specific"
    # Config subtypes
    SETUP_INTEGRATION = "setup-integration"
    CUSTOM_FORM = "custom-form"
    CONSULTATIVE = "consultative"
    REPORTING = "reporting"
    # FAQ subtypes
    EXISTING_ARTICLE = "existing-article"
    UPDATE_ARTICLE = "update-article"
    NEEDS_ARTICLE = "needs-article"
    # Feature-request subtypes
    NEW_CAPABILITY = "new-capability"
    ENHANCEMENT = "enhancement"


_SUBTYPES_BY_TYPE: dict[TicketType, tuple[TicketSubtype, ...]] = {
    TicketType.BUG: (TicketSubtype.PLATFORM_WIDE, TicketSubtype.CUSTOMER_SPECIFIC),
    TicketType.CONFIG: (
        TicketSubtype.SETUP_INTEGRATION,
        TicketSubtype.CUSTOM_FORM,
        TicketSubtype.CONSULTATIVE,
        TicketSubtype.REPORTING,
    ),
    TicketType.FAQ: (
        TicketSubtype.EXISTING_ARTICLE,
        TicketSubtype.UPDATE_ARTICLE,
        TicketSubtype.NEEDS_ARTICLE,
    ),
    TicketType.FEATURE_REQUEST: (
        TicketSubtype.NEW_CAPABILITY,
        TicketSubtype.ENHANCEMENT,
    ),
}


def subtypes_for(ticket_type: TicketType) -> tuple[TicketSubtype, ...]:
    return _SUBTYPES_BY_TYPE[ticket_type]


class TicketStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in-progress"
    AWAITING_CUSTOMER = "awaiting-customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


# Statuses still generating SLA scans / nudges. `RESOLVED` is *terminal* (the
# SE has confirmed the fix and the card retires immediately), so it is
# deliberately excluded — `query_live` filters on this set, keeping resolved
# tickets out of the SLA scan, and `applicable_stages` already treats RESOLVED
# as having no running clocks.
LIVE_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.NEW,
        TicketStatus.IN_PROGRESS,
        TicketStatus.AWAITING_CUSTOMER,
    }
)


class ResolutionType(StrEnum):
    """How a ticket was resolved — captured for reporting when the SE marks a
    ticket Resolved (plan Part 2). `CODE_CHANGE` optionally carries a PR link."""

    NO_CODE_CHANGE = "no-code-change"
    CODE_CHANGE = "code-change"


class Lane(StrEnum):
    SE_ACTION = "se-action"
    DEV_ACTION = "dev-action"


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


def bump_one_tier(current: Priority) -> Priority:
    """Bump priority up one tier; P0 stays P0 (flow §5c)."""
    order = (Priority.P4, Priority.P3, Priority.P2, Priority.P1, Priority.P0)
    idx = order.index(current)
    return order[min(idx + 1, len(order) - 1)]


class Severity(StrEnum):
    BLOCKING = "blocking"
    DEGRADED = "degraded"
    COSMETIC = "cosmetic"
    UNSURE = "unsure"


class Source(StrEnum):
    CUSTOMER_CHANNEL = "customer-channel"
    DM = "dm"
    CALL = "call"
    EMAIL = "email"
    IN_APP = "in-app"
    TECH_ASSISTANCE = "tech-assistance"


class ACVTier(StrEnum):
    SMALL = "small"
    MID = "mid"
    LARGE = "large"
    ENTERPRISE = "enterprise"


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class RenewalStatus(StrEnum):
    AT_RISK = "at-risk"
    STABLE = "stable"
    COMMITTED = "committed"
    UNKNOWN = "unknown"


class CustomerWeight(StrEnum):
    """Bucket of (ACV × sentiment × renewal) used as one axis of the prio matrix."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ArticleStatus(StrEnum):
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in-progress"
    LIVE = "live"
    NEEDS_UPDATE = "needs-update"
    REJECTED = "rejected"


class TicketLinkRelation(StrEnum):
    HOTFIX_OF = "hotfix-of"
    DUPE_OF = "dupe-of"
    ARTICLE_FOR = "article-for"
    SUPERSEDES = "supersedes"


class CommsDirection(StrEnum):
    INBOUND = "in"
    OUTBOUND = "out"


class SLATarget(BaseModel):
    """Soft SLA targets per priority tier (flow §5d).

    Hours unless noted. `None` means the stage is uncommitted for this tier
    (e.g. P4 has no resolution target). Business-day approximated as 24h
    continuous in v1; revisit per flow §18.
    """

    first_response_minutes: int
    status_update_hours: int | None = None
    resolution_hours: int | None = None
