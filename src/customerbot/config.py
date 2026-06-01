from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SlackConfig(BaseModel):
    bot_token: str
    signing_secret: str
    workspace_url: str = ""


class SLATarget(BaseModel):
    """Soft SLA targets per priority tier (see flow §5d).

    Hours unless noted. `None` on update/resolution means "no commitment".
    Business-day approximated as 24h continuous for v1; revisit per flow §18.
    """

    first_response_minutes: int
    status_update_hours: int | None = None
    resolution_hours: int | None = None


def _default_sla_targets() -> dict[str, SLATarget]:
    return {
        "P0": SLATarget(first_response_minutes=30, status_update_hours=2, resolution_hours=8),
        "P1": SLATarget(first_response_minutes=120, status_update_hours=24, resolution_hours=48),
        "P2": SLATarget(
            first_response_minutes=8 * 60, status_update_hours=48, resolution_hours=120
        ),
        "P3": SLATarget(
            first_response_minutes=24 * 60, status_update_hours=168, resolution_hours=240
        ),
        "P4": SLATarget(
            first_response_minutes=48 * 60, status_update_hours=None, resolution_hours=None
        ),
    }


class Settings(BaseSettings):
    slack: SlackConfig

    se_user_id: str | None = None
    ryan_user_id: str | None = None
    cto_user_id: str | None = None

    tech_assistance_channel_id: str | None = None
    se_tickets_channel_id: str | None = None
    support_ping_channel_id: str | None = None

    internal_user_group_id: str | None = None
    support_handle: str | None = None

    critical_path_features: list[str] = Field(default_factory=list)
    sla_targets: dict[str, SLATarget] = Field(default_factory=_default_sla_targets)
    prio_matrix_path: str | None = None

    inapp_webhook_secret: str | None = None

    se_timezone: str = "UTC"
    """IANA timezone used for the SE-local schedule of jobs like the monthly
    prio-matrix-review reminder. Defaults to UTC."""

    legacy_commands_enabled: bool = False

    database_path: str = "data/customerbot.db"
    host: str = "0.0.0.0"
    port: int = 8080
    reminder_hours: int = 24

    model_config = SettingsConfigDict(
        env_prefix="CUSTOMERBOT_",
        env_file=".env",
        env_nested_delimiter="__",
    )

    @model_validator(mode="after")
    def _resolve_se_user_id(self) -> Settings:
        if self.se_user_id is None and self.ryan_user_id is None:
            raise ValueError(
                "CUSTOMERBOT_SE_USER_ID (or legacy CUSTOMERBOT_RYAN_USER_ID) is required"
            )
        if self.se_user_id is None:
            self.se_user_id = self.ryan_user_id
        if self.ryan_user_id is None:
            self.ryan_user_id = self.se_user_id
        return self
