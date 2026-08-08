from __future__ import annotations

import pytest
from pydantic import ValidationError

from customerbot.config import Settings


def _required_slack_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOMERBOT_SLACK__BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("CUSTOMERBOT_SLACK__SIGNING_SECRET", "sigsec-test")


def _clear_se_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUSTOMERBOT_SE_USER_ID", raising=False)
    monkeypatch.delenv("CUSTOMERBOT_RYAN_USER_ID", raising=False)


def test_legacy_ryan_user_id_aliases_se_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_RYAN_USER_ID", "U_LEGACY")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.se_user_id == "U_LEGACY"
    assert s.ryan_user_id == "U_LEGACY"


def test_se_user_id_wins_when_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_SE_USER_ID", "U_NEW")
    monkeypatch.setenv("CUSTOMERBOT_RYAN_USER_ID", "U_OLD")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.se_user_id == "U_NEW"
    assert s.ryan_user_id == "U_OLD"


def test_missing_se_and_ryan_user_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_optional_v1_keys_default_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_SE_USER_ID", "U_SE")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.cto_user_id is None
    assert s.tech_assistance_channel_id is None
    assert s.se_tickets_channel_id is None
    assert s.support_ping_channel_id is None
    assert s.internal_user_group_id is None
    assert s.support_handle is None
    assert s.prio_matrix_path is None
    assert s.inapp_webhook_secret is None
    assert s.gleap_channel_id is None
    assert s.critical_path_features == []


def test_support_thread_channel_ids_combines_support_and_gleap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_SE_USER_ID", "U_SE")

    # Neither set → empty.
    assert Settings(_env_file=None).support_thread_channel_ids == ()  # type: ignore[call-arg]

    monkeypatch.setenv("CUSTOMERBOT_TECH_ASSISTANCE_CHANNEL_ID", "C_SUPPORT")
    monkeypatch.setenv("CUSTOMERBOT_GLEAP_CHANNEL_ID", "C_GLEAP")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.support_thread_channel_ids == ("C_SUPPORT", "C_GLEAP")

    # Same channel for both → deduped.
    monkeypatch.setenv("CUSTOMERBOT_GLEAP_CHANNEL_ID", "C_SUPPORT")
    assert Settings(_env_file=None).support_thread_channel_ids == ("C_SUPPORT",)  # type: ignore[call-arg]


def test_sla_targets_default_to_flow_spec_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_SE_USER_ID", "U_SE")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert set(s.sla_targets.keys()) == {"P0", "P1", "P2", "P3", "P4"}
    assert s.sla_targets["P0"].first_response_minutes == 30
    assert s.sla_targets["P1"].first_response_minutes == 120
    assert s.sla_targets["P4"].status_update_hours is None
    assert s.sla_targets["P4"].resolution_hours is None


def test_urgent_nag_window_defaults_to_waking_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_SE_USER_ID", "U_SE")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert (s.urgent_nag_start_hour, s.urgent_nag_end_hour) == (7, 23)


def test_urgent_nag_window_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_SE_USER_ID", "U_SE")
    monkeypatch.setenv("CUSTOMERBOT_URGENT_NAG_START_HOUR", "9")
    monkeypatch.setenv("CUSTOMERBOT_URGENT_NAG_END_HOUR", "18")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert (s.urgent_nag_start_hour, s.urgent_nag_end_hour) == (9, 18)


def test_critical_path_features_parses_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_slack_env(monkeypatch)
    _clear_se_env(monkeypatch)
    monkeypatch.setenv("CUSTOMERBOT_SE_USER_ID", "U_SE")
    monkeypatch.setenv("CUSTOMERBOT_CRITICAL_PATH_FEATURES", '["publishing", "scheduling"]')

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.critical_path_features == ["publishing", "scheduling"]
