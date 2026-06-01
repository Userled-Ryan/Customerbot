"""Integration tests for the in-app bug webhook endpoint."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from customerbot.application.intake.submissions import InAppBugSubmission
from customerbot.application.intake.submit_ticket_form import SubmitResult
from customerbot.domain.bot_state.entities import PendingDedupeChoice
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.webhooks.in_app_bug import (
    WEBHOOK_PATH,
    InAppBugWebhook,
)
from customerbot.integration.webhooks.signing import expected_signature

SECRET = "test-secret"

VALID_PAYLOAD: dict[str, Any] = {
    "org_id": "acme",
    "user_id": "U_CUSTOMER",
    "user_email": "user@acme.io",
    "page_url": "https://app.userled.io/campaigns/42",
    "description": "Filter dropdown won't open on the campaign page",
    "screenshot_url": "https://cdn.userled.io/screenshots/xyz.png",
    "session_replay_url": "https://replay.userled.io/abc",
}


@dataclass
class _FakeSubmit:
    """Test double for SubmitTicketForm — captures the submission and returns
    whatever was configured. Lets us drive HTTP-layer scenarios without
    spinning up the full intake pipeline."""

    next_result: SubmitResult = field(
        default_factory=lambda: SubmitResult(ticket=_make_ticket(), pending_dedupe=None)
    )
    raise_exc: bool = False
    calls: list[InAppBugSubmission] = field(default_factory=list)

    async def from_in_app_webhook(self, submission: InAppBugSubmission) -> SubmitResult:
        self.calls.append(submission)
        if self.raise_exc:
            raise RuntimeError("downstream blew up")
        return self.next_result


def _make_ticket(ticket_id: int = 7) -> Ticket:
    return Ticket(
        id=ticket_id,
        title="Filter dropdown won't open on the campaign page",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=TicketStatus.NEW,
        lane=Lane.SE_ACTION,
        priority=Priority.P3,
        severity=Severity.UNSURE,
        reporter_user_id="in-app-webhook:user@acme.io",
        source=Source.IN_APP,
        description="Filter dropdown won't open on the campaign page",
    )


def _signed_request(
    *,
    body: bytes,
    secret: str = SECRET,
    timestamp: str | None = None,
    signature: str | None = None,
) -> dict[str, str]:
    ts = timestamp if timestamp is not None else str(int(time.time()))
    sig = signature if signature is not None else expected_signature(secret, ts, body)
    return {
        "X-CustomerBot-Timestamp": ts,
        "X-CustomerBot-Signature": sig,
        "Content-Type": "application/json",
    }


def _make_app(submit: _FakeSubmit, *, secret: str | None = SECRET) -> FastAPI:
    app = FastAPI()
    webhook = InAppBugWebhook(submit_ticket_form=submit, inapp_webhook_secret=secret)  # type: ignore[arg-type]
    webhook.register_routes(app)
    return app


# --- Happy path -------------------------------------------------------------


def test_valid_signed_payload_accepted_and_routes_to_submit() -> None:
    submit = _FakeSubmit()
    client = TestClient(_make_app(submit))
    body = json.dumps(VALID_PAYLOAD).encode()
    response = client.post(WEBHOOK_PATH, content=body, headers=_signed_request(body=body))
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["ticket_id"] == "TIC-007"
    assert len(submit.calls) == 1
    captured = submit.calls[0]
    assert captured.org_id == "acme"
    assert captured.page_url == "https://app.userled.io/campaigns/42"
    assert captured.user_email == "user@acme.io"


def test_dedupe_pending_response_carries_pending_id() -> None:
    pending = PendingDedupeChoice(
        id=42,
        candidate_ticket_id=7,
        payload_json="{}",
        invoker_user_id="U_SE",
        dm_channel_id="D_SE",
        dm_message_ts="ts",
        expires_at=__import__("datetime").datetime(2030, 1, 1),
    )
    submit = _FakeSubmit(next_result=SubmitResult(ticket=None, pending_dedupe=pending))
    client = TestClient(_make_app(submit))
    body = json.dumps(VALID_PAYLOAD).encode()
    response = client.post(WEBHOOK_PATH, content=body, headers=_signed_request(body=body))
    assert response.status_code == 202
    assert response.json() == {"status": "accepted-pending-dedupe", "pending_dedupe_id": "42"}


# --- Signature failures -----------------------------------------------------


def test_missing_signature_headers_returns_401() -> None:
    submit = _FakeSubmit()
    client = TestClient(_make_app(submit))
    body = json.dumps(VALID_PAYLOAD).encode()
    response = client.post(
        WEBHOOK_PATH,
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json()["status"] == "unauthorized"
    assert response.json()["reason"] == "missing-headers"
    assert submit.calls == []


def test_bad_signature_returns_401() -> None:
    submit = _FakeSubmit()
    client = TestClient(_make_app(submit))
    body = json.dumps(VALID_PAYLOAD).encode()
    response = client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_signed_request(body=body, signature="deadbeef"),
    )
    assert response.status_code == 401
    assert response.json()["reason"] == "signature-mismatch"
    assert submit.calls == []


def test_stale_timestamp_returns_401() -> None:
    submit = _FakeSubmit()
    client = TestClient(_make_app(submit))
    body = json.dumps(VALID_PAYLOAD).encode()
    stale_ts = str(int(time.time()) - 999_999)
    response = client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_signed_request(body=body, timestamp=stale_ts),
    )
    assert response.status_code == 401
    assert response.json()["reason"] == "stale"
    assert submit.calls == []


def test_body_tampering_rejected_with_signature_mismatch() -> None:
    submit = _FakeSubmit()
    client = TestClient(_make_app(submit))
    original_body = json.dumps(VALID_PAYLOAD).encode()
    headers = _signed_request(body=original_body)
    tampered = original_body.replace(b"acme", b"globex")
    response = client.post(WEBHOOK_PATH, content=tampered, headers=headers)
    assert response.status_code == 401
    assert response.json()["reason"] == "signature-mismatch"
    assert submit.calls == []


# --- Payload + config failures ----------------------------------------------


def test_unconfigured_secret_returns_503() -> None:
    submit = _FakeSubmit()
    client = TestClient(_make_app(submit, secret=None))
    body = json.dumps(VALID_PAYLOAD).encode()
    response = client.post(WEBHOOK_PATH, content=body, headers=_signed_request(body=body))
    assert response.status_code == 503
    assert response.json()["status"] == "unconfigured"
    assert submit.calls == []


def test_invalid_payload_returns_400() -> None:
    submit = _FakeSubmit()
    client = TestClient(_make_app(submit))
    bad = {"org_id": "", "user_id": "x"}  # missing required fields
    body = json.dumps(bad).encode()
    response = client.post(WEBHOOK_PATH, content=body, headers=_signed_request(body=body))
    assert response.status_code == 400
    assert response.json()["status"] == "invalid-payload"
    assert submit.calls == []


def test_downstream_failure_returns_502() -> None:
    submit = _FakeSubmit(raise_exc=True)
    client = TestClient(_make_app(submit))
    body = json.dumps(VALID_PAYLOAD).encode()
    response = client.post(WEBHOOK_PATH, content=body, headers=_signed_request(body=body))
    assert response.status_code == 502
    assert response.json()["status"] == "downstream-failure"
    assert len(submit.calls) == 1  # we did try
