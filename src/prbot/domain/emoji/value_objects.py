from pydantic import BaseModel


class EmojiConfig(BaseModel):
    """Configurable emoji names for each PR status."""

    merged: str = "git-merged"
    closed: str = "headstone"
    changes_requested: str = "git-changes-requested"
    approved: str = "git-approved"
    commented: str = "speech_balloon"

    def for_status(self, status: str) -> str | None:
        """Return the emoji name for a PR status, or None for statuses with no reaction."""
        mapping: dict[str, str] = {
            "merged": self.merged,
            "closed": self.closed,
            "changes_requested": self.changes_requested,
            "approved": self.approved,
            "commented": self.commented,
        }
        return mapping.get(status)
