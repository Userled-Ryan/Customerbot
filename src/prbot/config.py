from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings


class SlackConfig(BaseModel):
    bot_token: str
    signing_secret: str
    workspace_url: str = ""


class Settings(BaseSettings):
    slack: SlackConfig
    ryan_user_id: str
    database_path: str = "data/customerbot.db"
    host: str = "0.0.0.0"
    port: int = 8080
    reminder_hours: int = 24

    model_config = {
        "env_prefix": "CUSTOMERBOT_",
        "env_file": ".env",
        "env_nested_delimiter": "__",
    }
