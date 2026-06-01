"""HMAC-SHA256 signature verification for incoming webhooks (ambiguity #3).

Stripe-style scheme — two headers carry the auth:

- `X-CustomerBot-Timestamp`: unix-seconds when the sender signed the request.
- `X-CustomerBot-Signature`: `hexdigest(HMAC-SHA256(secret, f"{ts}.{body}"))`.

Verification rejects:
- Missing headers
- Bodies older than `max_age_seconds` (default 5 min — replay protection)
- Signatures that don't match under constant-time comparison

Returning a discrete `VerificationResult` (vs raising) lets the FastAPI
handler turn outcomes into specific HTTP responses without catching
exceptions.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from enum import StrEnum

DEFAULT_MAX_AGE_SECONDS = 5 * 60


class VerificationOutcome(StrEnum):
    OK = "ok"
    MISSING_HEADERS = "missing-headers"
    BAD_TIMESTAMP = "bad-timestamp"
    STALE = "stale"
    SIGNATURE_MISMATCH = "signature-mismatch"


@dataclass(frozen=True)
class VerificationResult:
    outcome: VerificationOutcome
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == VerificationOutcome.OK


def expected_signature(secret: str, timestamp: str, body: bytes) -> str:
    """Compute the hex HMAC-SHA256 the sender should have produced."""
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    secret: str,
    timestamp_header: str | None,
    signature_header: str | None,
    body: bytes,
    now: float | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> VerificationResult:
    """Verify an incoming webhook signature. Pure — no I/O."""
    if not timestamp_header or not signature_header:
        return VerificationResult(
            VerificationOutcome.MISSING_HEADERS,
            "X-CustomerBot-Timestamp and X-CustomerBot-Signature are required",
        )
    try:
        ts = int(timestamp_header)
    except ValueError:
        return VerificationResult(
            VerificationOutcome.BAD_TIMESTAMP,
            f"timestamp header is not an integer: {timestamp_header!r}",
        )
    current = now if now is not None else time.time()
    if abs(current - ts) > max_age_seconds:
        return VerificationResult(
            VerificationOutcome.STALE,
            f"timestamp {ts} is more than {max_age_seconds}s from now ({int(current)})",
        )
    expected = expected_signature(secret, timestamp_header, body)
    if not hmac.compare_digest(expected, signature_header):
        return VerificationResult(
            VerificationOutcome.SIGNATURE_MISMATCH,
            "computed signature does not match X-CustomerBot-Signature",
        )
    return VerificationResult(VerificationOutcome.OK)
