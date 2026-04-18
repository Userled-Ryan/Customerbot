from __future__ import annotations

from pydantic import BaseModel

from prbot.domain.tracking.value_objects import MessageRef, PRUrl


class TrackedPR(BaseModel):
    """A PR being tracked in a messaging platform.

    This is the aggregate root — it knows which message contains
    the PR URL, and which emoji reactions have been applied.
    """

    pr_url: PRUrl
    message_ref: MessageRef
    applied_emojis: frozenset[str] = frozenset()
    scope_keys: tuple[str, ...] = ()

    def has_emoji(self, emoji: str) -> bool:
        """Check if a specific emoji has already been applied."""
        return emoji in self.applied_emojis

    def with_added_emoji(self, emoji: str) -> TrackedPR:
        """Return a new TrackedPR with an emoji added."""
        return self.model_copy(update={"applied_emojis": self.applied_emojis | {emoji}})
