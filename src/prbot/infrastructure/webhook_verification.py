import hashlib
import hmac


def verify_github_signature(
    payload_body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    """Verify the HMAC-SHA256 signature of a GitHub webhook payload.

    Returns True if valid, False otherwise.
    """
    if not signature_header:
        return False

    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            msg=payload_body,
            digestmod=hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected, signature_header)
