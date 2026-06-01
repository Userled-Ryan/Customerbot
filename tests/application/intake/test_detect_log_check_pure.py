"""Pure tests for the log/check detector's regex helpers + payload codec.

No DB / Slack dependencies — these are the building blocks that decide whether
the detector fires at all, plus the button-value (de)serialiser.
"""

from __future__ import annotations

from customerbot.application.intake.detect_log_check import (
    DetectorPayload,
    app_mention_triggers,
    decode_payload,
    encode_payload,
    match_trigger_word,
)

# --- Trigger matching ---


def test_log_matches() -> None:
    assert match_trigger_word("Let me log this") == "log"


def test_check_matches() -> None:
    assert match_trigger_word("I'll check on it") == "check"


def test_match_is_case_insensitive() -> None:
    assert match_trigger_word("LOG this") == "log"
    assert match_trigger_word("Check on this") == "check"


def test_logger_does_not_match() -> None:
    """Word boundary — `logger` should not trigger on `log`."""
    assert match_trigger_word("Add a logger to that") is None


def test_checking_does_not_match() -> None:
    assert match_trigger_word("They're checking the issue") is None


def test_nolog_concatenated_does_not_match() -> None:
    """Word boundary — `nolog` is one token, no match either way."""
    assert match_trigger_word("nolog please") is None


def test_no_log_negation_suppresses() -> None:
    assert match_trigger_word("no log needed") is None


def test_no_check_negation_suppresses() -> None:
    assert match_trigger_word("no check required") is None


def test_negation_suppresses_even_when_other_trigger_present() -> None:
    """Per spec: `no log` / `no check` short-circuits the whole message."""
    assert match_trigger_word("no log on this, but maybe log later") is None


def test_empty_string() -> None:
    assert match_trigger_word("") is None


# --- app_mention `log this` override ---


def test_app_mention_log_this_triggers() -> None:
    assert app_mention_triggers("<@U_BOT> log this please") is True


def test_app_mention_random_text_does_not_trigger() -> None:
    assert app_mention_triggers("<@U_BOT> hello") is False


def test_app_mention_log_alone_does_not_trigger() -> None:
    """The override requires 'log this', not just 'log'."""
    assert app_mention_triggers("<@U_BOT> log") is False


# --- Payload codec ---


def test_encode_decode_round_trip() -> None:
    payload = DetectorPayload(
        channel_id="C_ACME",
        thread_ts="1700.123",
        permalink="https://x.slack.com/archives/C_ACME/p1700123",
        description="Customer reports publishing is broken",
        org_id="acme",
    )
    encoded = encode_payload(payload)
    decoded = decode_payload(encoded)
    assert decoded == payload


def test_encode_truncates_huge_description() -> None:
    huge = "x" * 5000
    payload = DetectorPayload(
        channel_id="C", thread_ts="1.0", permalink="p", description=huge, org_id=None
    )
    encoded = encode_payload(payload)
    # Slack's hard limit is 2000; encoder targets <= 1900.
    assert len(encoded) <= 1950
    decoded = decode_payload(encoded)
    assert len(decoded.description) < len(huge)


def test_decode_handles_null_org_id() -> None:
    payload = DetectorPayload(
        channel_id="C", thread_ts="1.0", permalink="p", description="d", org_id=None
    )
    decoded = decode_payload(encode_payload(payload))
    assert decoded.org_id is None
