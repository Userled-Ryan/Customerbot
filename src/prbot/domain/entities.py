from pydantic import BaseModel

from prbot.domain.value_objects import EmojiReaction, PRUrl


class TrackedPR(BaseModel):
    """A PR being tracked in a Slack channel.

    This is the aggregate root — it knows which Slack message contains
    the PR URL, what channel it is in, and what emoji is currently applied.
    """

    pr_url: PRUrl
    channel_id: str
    message_ts: str
    current_emoji: EmojiReaction | None = None

    def needs_update(self, new_emoji: EmojiReaction) -> bool:
        """Check if the emoji needs to change."""
        return self.current_emoji != new_emoji

    def with_emoji(self, emoji: EmojiReaction) -> TrackedPR:
        """Return a new TrackedPR with the emoji updated."""
        return self.model_copy(update={"current_emoji": emoji})
