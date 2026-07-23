"""DTOs for normalized form submissions.

Sits in the application layer so use cases can consume them without reaching
into the integration adapter. Slack-shape parsing lives next to the modal
builders in `integration/slack/modals/submission_payload.py`, which produces
these.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from customerbot.domain.tickets.value_objects import (
    Source,
    TicketSubtype,
    TicketType,
)


# DORMANT (2026-07-02): CSM intake modal retired — see csm_intake.py header.
# REMOVE with the rest of that path if we don't revert.
@dataclass
class CSMIntakeSubmission:
    description: str
    org_id: str
    prod_link: str
    blocking: bool
    deadline: date | None
    blocking_impact: str | None


@dataclass
class SEBugSubmission:
    org_id: str
    source: Source
    summary: str
    description: str
    blocking: bool
    deadline: date | None
    affected_user: str | None
    replay_link: str | None
    # Campaign this ticket relates to, when the SE answered "Is part of
    # campaign? = Yes". None when No. Stored in a dedicated Ticket field (kept
    # out of prod_link so it doesn't feed exact-match dedupe).
    campaign_url: str | None = None
    # Bug (default) or Config. Config = a non-bug SE action (enable a feature
    # flag, verify a domain, etc.). Drives type/subtype and priority downstream.
    ticket_type: TicketType = TicketType.BUG
    # Explicit platform-wide flag (SE checkbox). For Bug tickets this picks the
    # subtype: platform-wide vs customer-specific. Ignored for Config.
    platform_wide: bool = False
    # Urgent flag (SE checkbox). When set the ticket is forced to P1, its
    # deadline is dropped, it's assigned to the configured SE, and it mirrors
    # into Linear's Urgent section — the replacement for sub-48h deadlines.
    urgent: bool = False
    # Set when the org dropdown's "Create new org…" option was chosen. The
    # submit handler creates the org from the fields below (owner defaults to
    # the reporter) and rewrites `org_id` to the new org before logging.
    create_new_org: bool = False
    new_org_name: str | None = None
    new_org_channel_id: str | None = None
    new_org_owner_id: str | None = None


@dataclass
class ReclassifySubmission:
    ticket_id: int
    new_type: TicketType
    new_subtype: TicketSubtype
    reason: str
    next_step: str
    owner_user_id: str


@dataclass
class InAppBugSubmission:
    """A bug submission delivered via the in-product webhook (min-spec §3c)."""

    org_id: str
    user_id: str
    user_email: str
    page_url: str
    description: str
    screenshot_url: str | None
    session_replay_url: str | None
