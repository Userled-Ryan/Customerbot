from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings

from prbot.domain.value_objects import EmojiConfig


class SlackConfig(BaseModel):
    """Configuration for the Slack integration."""

    bot_token: str
    signing_secret: str


class DiscordConfig(BaseModel):
    """Configuration for the Discord integration."""

    bot_token: str


class Settings(BaseSettings):
    """Application settings loaded from environment variables with PR_BOT_ prefix."""

    slack: SlackConfig | None = None
    discord: DiscordConfig | None = None
    github_app_id: str
    github_private_key: str
    github_webhook_secret: str
    database_path: str = "data/pr_bot.db"
    host: str = "0.0.0.0"
    port: int = 8080
    emoji: EmojiConfig = EmojiConfig()

    model_config = {"env_prefix": "PR_BOT_", "env_file": ".env", "env_nested_delimiter": "__"}
