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
    Severity,
    Source,
    TicketSubtype,
    TicketType,
)


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
    severity: Severity
    affected_user: str | None
    replay_link: str | None


@dataclass
class ReclassifySubmission:
    ticket_id: int
    new_type: TicketType
    new_subtype: TicketSubtype
    reason: str
    next_step: str
    owner_user_id: str
