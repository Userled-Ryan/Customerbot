from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings


class EmojiConfig(BaseModel):
    """Configurable emoji names for each PR status."""

    merged: str = "tada"
    closed: str = "x"
    changes_requested: str = "arrows_counterclockwise"
    approved: str = "white_check_mark"
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


class Settings(BaseSettings):
    """Application settings loaded from environment variables with PR_BOT_ prefix."""

    slack_bot_token: str
    slack_signing_secret: str
    github_token: str
    github_webhook_secret: str
    database_path: str = "data/pr_bot.db"
    host: str = "0.0.0.0"
    port: int = 8080
    emoji: EmojiConfig = EmojiConfig()

    model_config = {"env_prefix": "PR_BOT_", "env_file": ".env", "env_nested_delimiter": "_"}
