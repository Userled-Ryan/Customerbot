from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from customerbot.domain.tracking.value_objects import ConversationCategory, ConversationStatus


class TrackedConversation(BaseModel):
    """A customer conversation thread being monitored.

    Created when Ryan is mentioned in a thread, or when Ryan replies in one.
    """

    id: int | None = None
    channel_id: str
    thread_ts: str
    channel_name: str = ""
    category: ConversationCategory = ConversationCategory.OTHER
    status: ConversationStatus = ConversationStatus.OPEN
    context: str = ""
    last_ryan_reply_at: datetime | None = None
    opened_at: datetime = datetime.utcnow()
    reminder_sent_at: datetime | None = None

    def is_overdue(self, hours: int) -> bool:
        """Return True if Ryan hasn't replied within the given SLA window."""
        from datetime import timedelta

        now = datetime.utcnow()
        reference = self.last_ryan_reply_at or self.opened_at
        return (now - reference) > timedelta(hours=hours)

    def hours_since_last_reply(self) -> float:
        from datetime import timedelta

        reference = self.last_ryan_reply_at or self.opened_at
        delta = datetime.utcnow() - reference
        return delta.total_seconds() / 3600
