"""Signature verification for inbound Linear webhooks (v1.5).

Linear signs each delivery with HMAC-SHA256 over the **raw request body**, hex
digest in the `Linear-Signature` header. This differs from the in-app webhook
scheme (`webhooks/signing.py`, a timestamp-prefixed Stripe-style MAC), so it
needs its own verifier — but it reuses that module's `VerificationResult` /
`VerificationOutcome` types so the FastAPI handler maps outcomes to HTTP codes
the same way.

Linear also embeds a `webhookTimestamp` (epoch ms) in the JSON body for replay
protection; `verify_linear_signature` optionally rejects bodies older than
`max_age_seconds` when a timestamp is supplied.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from customerbot.integration.webhooks.signing import (
    DEFAULT_MAX_AGE_SECONDS,
    VerificationOutcome,
    VerificationResult,
)


def expected_signature(secret: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of the raw body — what Linear should have sent."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_linear_signature(
    *,
    secret: str,
    signature_header: str | None,
    body: bytes,
    webhook_timestamp_ms: int | None = None,
    now: float | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> VerificationResult:
    """Verify a Linear webhook signature. Pure — no I/O."""
    if not signature_header:
        return VerificationResult(
            VerificationOutcome.MISSING_HEADERS, "Linear-Signature header is required"
        )
    if webhook_timestamp_ms is not None:
        current = now if now is not None else time.time()
        age = abs(current - webhook_timestamp_ms / 1000)
        if age > max_age_seconds:
            return VerificationResult(
                VerificationOutcome.STALE,
                f"webhookTimestamp is more than {max_age_seconds}s from now",
            )
    expected = expected_signature(secret, body)
    if not hmac.compare_digest(expected, signature_header):
        return VerificationResult(
            VerificationOutcome.SIGNATURE_MISMATCH,
            "computed signature does not match Linear-Signature",
        )
    return VerificationResult(VerificationOutcome.OK)
