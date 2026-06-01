"""Unit tests for HMAC signature verification (Chunk 14)."""

from __future__ import annotations

import time

from customerbot.integration.webhooks.signing import (
    DEFAULT_MAX_AGE_SECONDS,
    VerificationOutcome,
    expected_signature,
    verify_signature,
)

SECRET = "test-secret"


def test_valid_signature_passes() -> None:
    body = b'{"hello":"world"}'
    ts = str(int(time.time()))
    sig = expected_signature(SECRET, ts, body)
    result = verify_signature(
        secret=SECRET,
        timestamp_header=ts,
        signature_header=sig,
        body=body,
    )
    assert result.ok
    assert result.outcome == VerificationOutcome.OK


def test_missing_timestamp_rejected() -> None:
    result = verify_signature(
        secret=SECRET,
        timestamp_header=None,
        signature_header="abc",
        body=b"x",
    )
    assert not result.ok
    assert result.outcome == VerificationOutcome.MISSING_HEADERS


def test_missing_signature_rejected() -> None:
    result = verify_signature(
        secret=SECRET,
        timestamp_header=str(int(time.time())),
        signature_header=None,
        body=b"x",
    )
    assert not result.ok
    assert result.outcome == VerificationOutcome.MISSING_HEADERS


def test_non_numeric_timestamp_rejected() -> None:
    result = verify_signature(
        secret=SECRET,
        timestamp_header="not-a-number",
        signature_header="abc",
        body=b"x",
    )
    assert result.outcome == VerificationOutcome.BAD_TIMESTAMP


def test_stale_timestamp_rejected() -> None:
    now = 1_700_000_000.0
    ts = str(int(now - DEFAULT_MAX_AGE_SECONDS - 1))
    body = b"x"
    sig = expected_signature(SECRET, ts, body)
    result = verify_signature(
        secret=SECRET,
        timestamp_header=ts,
        signature_header=sig,
        body=body,
        now=now,
    )
    assert result.outcome == VerificationOutcome.STALE


def test_future_timestamp_also_rejected() -> None:
    """Skew the other direction — if the sender's clock is way ahead,
    we still treat it as stale (replay protection cuts both ways)."""
    now = 1_700_000_000.0
    ts = str(int(now + DEFAULT_MAX_AGE_SECONDS + 10))
    body = b"x"
    sig = expected_signature(SECRET, ts, body)
    result = verify_signature(
        secret=SECRET,
        timestamp_header=ts,
        signature_header=sig,
        body=body,
        now=now,
    )
    assert result.outcome == VerificationOutcome.STALE


def test_signature_mismatch_rejected() -> None:
    body = b"x"
    ts = str(int(time.time()))
    result = verify_signature(
        secret=SECRET,
        timestamp_header=ts,
        signature_header="deadbeef",
        body=body,
    )
    assert result.outcome == VerificationOutcome.SIGNATURE_MISMATCH


def test_body_tampering_invalidates_signature() -> None:
    """Same timestamp + signature but different body must fail."""
    ts = str(int(time.time()))
    sig = expected_signature(SECRET, ts, b"original")
    result = verify_signature(
        secret=SECRET,
        timestamp_header=ts,
        signature_header=sig,
        body=b"tampered",
    )
    assert result.outcome == VerificationOutcome.SIGNATURE_MISMATCH


def test_wrong_secret_invalidates_signature() -> None:
    body = b"x"
    ts = str(int(time.time()))
    sig = expected_signature("other-secret", ts, body)
    result = verify_signature(
        secret=SECRET,
        timestamp_header=ts,
        signature_header=sig,
        body=body,
    )
    assert result.outcome == VerificationOutcome.SIGNATURE_MISMATCH
