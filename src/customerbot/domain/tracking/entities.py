from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from customerbot.domain.tracking.value_objects import ConversationStatus


def _utcnow() -> datetime:
    """Naive UTC now (replaces deprecated datetime.utcnow())."""
    return datetime.now(UTC).replace(tzinfo=None)


class TrackedConversation(BaseModel):
    """A customer conversation thread being monitored.

    Created when Ryan is mentioned in a thread, or when Ryan replies in one.
    """

    id: int | None = None
    ticket_number: int | None = None
    channel_id: str
    thread_ts: str
    channel_name: str = ""
    category: str = "other"
    status: ConversationStatus = ConversationStatus.OPEN
    context: str = ""
    last_ryan_reply_at: datetime | None = None
    opened_at: datetime = _utcnow()
    reminder_sent_at: datetime | None = None
    reminder_interval_hours: int | None = None  # None = use user's default

    def effective_reminder_hours(self, default: int) -> int:
        return self.reminder_interval_hours if self.reminder_interval_hours is not None else default

    def is_overdue(self, hours: int) -> bool:
        """Return True if Ryan hasn't replied within the given SLA window."""
        from datetime import timedelta

        now = _utcnow()
        reference = self.last_ryan_reply_at or self.opened_at
        return (now - reference) > timedelta(hours=hours)

    def hours_since_last_reply(self) -> float:
        reference = self.last_ryan_reply_at or self.opened_at
        delta = _utcnow() - reference
        return delta.total_seconds() / 3600


class UserSettings(BaseModel):
    user_id: str
    timezone: str = "UTC"
    default_reminder_hours: int = 24
    daily_digest_enabled: bool = True
    last_morning_digest_date: str | None = None  # ISO date in user's TZ
    last_evening_digest_date: str | None = None  # ISO date in user's TZ
