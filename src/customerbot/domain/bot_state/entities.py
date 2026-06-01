from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- Draft form sessions (§3a anti-phantom 30-min rule) ---


class ModalKind(StrEnum):
    CSM_INTAKE = "csm_intake"
    SE_BUG = "se_bug"
    RECLASSIFY = "reclassify"


class DraftFormSession(BaseModel):
    id: int | None = None
    slack_view_id: str
    modal_kind: ModalKind
    invoker_user_id: str
    invoker_channel_id: str | None = None
    invoker_thread_ts: str | None = None
    payload_json: str = "{}"
    created_at: datetime = _utcnow()
    expires_at: datetime


# --- Channel → Org cache (ambiguity #1) ---


class ChannelOrgEntry(BaseModel):
    slack_channel_id: str
    org_id: str | None  # None = "no org matches this channel"
    last_synced_at: datetime


# --- SLA DM throttling (§8b "once per state per stage") ---


class SLAStage(StrEnum):
    FIRST_RESPONSE = "first_response"
    STATUS_UPDATE = "status_update"
    RESOLUTION = "resolution"
    # Pre-auto-close CSM nudges (Chunk 8 §9d). Stored in the same sla_dm_state
    # table; presence of a row signifies "nudge sent". Decoupled from the
    # green/amber/red SLA clocks above — `last_state` is informational only
    # for these stages.
    AWAITING_NUDGE_7D = "awaiting_nudge_7d"
    AWAITING_NUDGE_3D = "awaiting_nudge_3d"
    AWAITING_NUDGE_1D = "awaiting_nudge_1d"


class SLAState(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class SLADMRecord(BaseModel):
    ticket_id: int
    stage: SLAStage
    last_state: SLAState
    last_dm_at: datetime | None = None
    updated_at: datetime = _utcnow()


# --- Pending dedupe choice (§11 "Merge / Create new") ---


class PendingDedupeChoice(BaseModel):
    id: int | None = None
    candidate_ticket_id: int
    payload_json: str
    invoker_user_id: str
    dm_channel_id: str
    dm_message_ts: str
    created_at: datetime = _utcnow()
    expires_at: datetime


# --- Pending priority override (§7a override buttons) ---


class PendingPrioOverride(BaseModel):
    id: int | None = None
    ticket_id: int
    suggested_priority: str  # Priority enum value
    dm_channel_id: str
    dm_message_ts: str
    created_at: datetime = _utcnow()
    expires_at: datetime


# --- Pending reclassification "Send" (§10) ---


class PendingReclassifySend(BaseModel):
    id: int | None = None
    ticket_id: int
    reclassification_event_id: int
    recipients_json: str
    draft_text: str
    dm_channel_id: str
    dm_message_ts: str
    created_at: datetime = _utcnow()
    expires_at: datetime


# --- Prio matrix review state (decision #4 monthly reminder) ---


class PrioMatrixReviewState(BaseModel):
    """Singleton; tracks the last monthly weightings-review ack / snooze."""

    last_ack_at: datetime | None = None
    last_snooze_until: datetime | None = None
    updated_at: datetime = _utcnow()
