from __future__ import annotations

import time

from customerbot.integration.linear.signing import (
    expected_signature,
    verify_linear_signature,
)
from customerbot.integration.webhooks.signing import VerificationOutcome

SECRET = "shhh"


def test_valid_signature_accepted() -> None:
    body = b'{"action":"update","type":"Issue"}'
    sig = expected_signature(SECRET, body)
    result = verify_linear_signature(secret=SECRET, signature_header=sig, body=body)
    assert result.ok


def test_missing_signature_rejected() -> None:
    result = verify_linear_signature(secret=SECRET, signature_header=None, body=b"{}")
    assert result.outcome == VerificationOutcome.MISSING_HEADERS


def test_tampered_body_rejected() -> None:
    sig = expected_signature(SECRET, b'{"a":1}')
    result = verify_linear_signature(secret=SECRET, signature_header=sig, body=b'{"a":2}')
    assert result.outcome == VerificationOutcome.SIGNATURE_MISMATCH


def test_stale_timestamp_rejected() -> None:
    body = b"{}"
    sig = expected_signature(SECRET, body)
    old_ms = int((time.time() - 3600) * 1000)
    result = verify_linear_signature(
        secret=SECRET, signature_header=sig, body=body, webhook_timestamp_ms=old_ms
    )
    assert result.outcome == VerificationOutcome.STALE
