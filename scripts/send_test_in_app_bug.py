"""Fire a signed test submission at the in-app-bug webhook (§3c).

Signs with the exact scheme `integration/webhooks/signing.py` verifies —
`HMAC-SHA256(secret, f"{ts}.".encode() + body)` — inlined so this stays a
zero-dependency stdlib script that runs anywhere (no package install needed).
Reads the shared secret from `CUSTOMERBOT_INAPP_WEBHOOK_SECRET` (same env var
the app reads), so on the Fly machine it Just Works against localhost without
you ever handling the secret.

This creates a REAL ticket: it posts a ticket card to SE_TICKETS_CHANNEL_ID and
a feed entry to TECH_ASSISTANCE_CHANNEL_ID, and mirrors into Linear. Use a
recognisably-test org/description so it's obvious on the board.

Usage (against the live bot, from inside the machine — secret already in env):

    fly ssh console -a customerbot-userled -C \
        "uv run --no-sync python scripts/send_test_in_app_bug.py \
            --url http://localhost:8080 --org-id acme"

Usage (locally, pointing at prod, secret supplied explicitly):

    CUSTOMERBOT_INAPP_WEBHOOK_SECRET=... uv run python scripts/send_test_in_app_bug.py \
        --url https://customerbot-userled.fly.dev

Every field is overridable; defaults describe a plausible in-app bug and mark
the submission as a test.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

WEBHOOK_PATH = "/webhooks/in-app-bug"


def expected_signature(secret: str, timestamp: str, body: bytes) -> str:
    """Mirror of `integration/webhooks/signing.py:expected_signature`."""
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://localhost:8080", help="Base URL of the bot")
    p.add_argument(
        "--secret",
        default=os.environ.get("CUSTOMERBOT_INAPP_WEBHOOK_SECRET"),
        help="HMAC secret (defaults to CUSTOMERBOT_INAPP_WEBHOOK_SECRET)",
    )
    p.add_argument("--org-id", default="acme", help="Customer org_id (falls back to 'unknown')")
    p.add_argument("--user-id", default="U_TEST_INAPP")
    p.add_argument("--user-email", default="test.user@example.com")
    p.add_argument("--page-url", default="https://app.userled.io/campaigns/42")
    p.add_argument(
        "--description",
        default="[TEST] Filter dropdown won't open on the campaign page",
    )
    p.add_argument("--screenshot-url", default="https://cdn.userled.io/screenshots/test.png")
    p.add_argument("--session-replay-url", default="https://replay.userled.io/test")
    # Corruption knobs for exercising the failure paths.
    p.add_argument("--tamper", action="store_true", help="Mutate body after signing → expect 401")
    p.add_argument("--stale", action="store_true", help="Sign with an old timestamp → expect 401")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.secret:
        raise SystemExit(
            "No secret. Set CUSTOMERBOT_INAPP_WEBHOOK_SECRET or pass --secret. "
            "On the Fly machine it's already in env; run this via `fly ssh console`."
        )

    payload = {
        "org_id": args.org_id,
        "user_id": args.user_id,
        "user_email": args.user_email,
        "page_url": args.page_url,
        "description": args.description,
        "screenshot_url": args.screenshot_url,
        "session_replay_url": args.session_replay_url,
    }
    body = json.dumps(payload).encode()

    ts = str(int(time.time()) - (999_999 if args.stale else 0))
    signature = expected_signature(args.secret, ts, body)

    sent_body = body.replace(b"campaign", b"tampered") if args.tamper else body

    url = args.url.rstrip("/") + WEBHOOK_PATH
    req = urllib.request.Request(
        url,
        data=sent_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-CustomerBot-Timestamp": ts,
            "X-CustomerBot-Signature": signature,
        },
    )
    print(f"POST {url}")
    print(f"  payload: {json.dumps(payload)}")
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted URL)
            print(f"  → {resp.status} {resp.read().decode()}")
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        print(f"  → {exc.code} {exc.read().decode()}")


if __name__ == "__main__":
    main()
