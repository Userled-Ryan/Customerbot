"""POST /webhooks/in-app-bug — in-product bug submission entry point.

The Userled web app fires a signed JSON webhook (§3c) when a user files
a bug from inside the product. We verify the HMAC, parse the payload,
hand off to `SubmitTicketForm.from_in_app_webhook`, and return 202.

Status code conventions:
- 202 — accepted, ticket creation queued
- 400 — payload couldn't be parsed
- 401 — signature missing / stale / mismatched
- 502 — downstream ticket creation failed (rare; we log and let the
        sender retry, idempotency is handled by Chunk 6's dedupe)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, Header, Request, Response
from pydantic import BaseModel, Field, ValidationError

from customerbot.application.intake.submissions import InAppBugSubmission
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.integration.webhooks.signing import (
    verify_signature,
)

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/in-app-bug"


class InAppBugPayload(BaseModel):
    """§3c payload schema. Validates required fields and types on entry."""

    org_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    user_email: str = ""
    page_url: str = Field(min_length=1)
    description: str = Field(min_length=1)
    screenshot_url: str | None = None
    session_replay_url: str | None = None

    def to_submission(self) -> InAppBugSubmission:
        return InAppBugSubmission(
            org_id=self.org_id,
            user_id=self.user_id,
            user_email=self.user_email,
            page_url=self.page_url,
            description=self.description,
            screenshot_url=self.screenshot_url or None,
            session_replay_url=self.session_replay_url or None,
        )


class InAppBugWebhook:
    """Owns the FastAPI router for `POST /webhooks/in-app-bug`."""

    def __init__(
        self,
        *,
        submit_ticket_form: SubmitTicketForm,
        inapp_webhook_secret: str | None,
    ) -> None:
        self._submit = submit_ticket_form
        self._secret = inapp_webhook_secret

    def build_router(self) -> APIRouter:
        router = APIRouter()

        @router.post(WEBHOOK_PATH, status_code=202)
        async def receive(
            request: Request,
            response: Response,
            x_customerbot_timestamp: str | None = Header(default=None),
            x_customerbot_signature: str | None = Header(default=None),
        ) -> dict[str, str]:
            if self._secret is None:
                # Fail closed — without a configured secret the endpoint can't
                # safely accept anything. Surface as 503 so the sender knows
                # the bot side is misconfigured rather than rejecting them.
                response.status_code = 503
                logger.warning("In-app webhook hit but INAPP_WEBHOOK_SECRET is not set; refusing")
                return {"status": "unconfigured"}

            body = await request.body()
            verification = verify_signature(
                secret=self._secret,
                timestamp_header=x_customerbot_timestamp,
                signature_header=x_customerbot_signature,
                body=body,
            )
            if not verification.ok:
                response.status_code = 401
                logger.info(
                    "In-app webhook rejected: %s (%s)",
                    verification.outcome.value,
                    verification.detail,
                )
                return {"status": "unauthorized", "reason": verification.outcome.value}

            try:
                payload = InAppBugPayload.model_validate_json(body)
            except ValidationError as exc:
                response.status_code = 400
                logger.info("In-app webhook payload validation failed: %s", exc)
                return {"status": "invalid-payload"}

            try:
                result = await self._submit.from_in_app_webhook(payload.to_submission())
            except Exception:
                response.status_code = 502
                logger.exception("In-app webhook ticket creation failed")
                return {"status": "downstream-failure"}

            if result.pending_dedupe is not None:
                return {
                    "status": "accepted-pending-dedupe",
                    "pending_dedupe_id": str(result.pending_dedupe.id or ""),
                }
            return {
                "status": "accepted",
                "ticket_id": result.ticket.display_id if result.ticket else "",
            }

        return router

    def register_routes(self, app: FastAPI) -> None:
        app.include_router(self.build_router())
