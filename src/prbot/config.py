from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables with PR_BOT_ prefix."""

    slack_bot_token: str
    slack_signing_secret: str
    github_token: str
    github_webhook_secret: str
    database_path: str = "data/pr_bot.db"
    host: str = "0.0.0.0"
    port: int = 8080

    model_config = {"env_prefix": "PR_BOT_", "env_file": ".env"}
