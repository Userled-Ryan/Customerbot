"""POST /webhooks/linear — inbound Linear webhook receiver (v1.5).

Mirrors `webhooks/in_app_bug.py`: verify the signature, parse the payload, map
the issue back to a customerbot ticket, and hand a normalised event to
`LinearInboundHandler`. Modelled status codes:

- 202 — accepted (handled, or intentionally ignored: unknown issue / no-op type)
- 401 — signature missing / stale / mismatched
- 503 — webhook secret not configured (fail closed)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Header, Request, Response

from customerbot.application.linear.inbound import LinearInboundEvent, LinearInboundHandler
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.integration.linear.mapping import linear_state_type_to_workflow_state
from customerbot.integration.linear.signing import verify_linear_signature

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/linear"


class LinearWebhook:
    def __init__(
        self,
        *,
        inbound: LinearInboundHandler,
        tickets: TicketRepositoryPort,
        webhook_secret: str | None,
    ) -> None:
        self._inbound = inbound
        self._tickets = tickets
        self._secret = webhook_secret

    def build_router(self) -> APIRouter:
        router = APIRouter()

        @router.post(WEBHOOK_PATH, status_code=202)
        async def receive(
            request: Request,
            response: Response,
            linear_signature: str | None = Header(default=None),
        ) -> dict[str, str]:
            if self._secret is None:
                response.status_code = 503
                logger.warning("Linear webhook hit but LINEAR__WEBHOOK_SECRET unset; refusing")
                return {"status": "unconfigured"}

            body = await request.body()
            try:
                payload: dict[str, Any] = json.loads(body)
            except ValueError:
                payload = {}

            verification = verify_linear_signature(
                secret=self._secret,
                signature_header=linear_signature,
                body=body,
                webhook_timestamp_ms=_as_int(payload.get("webhookTimestamp")),
            )
            if not verification.ok:
                response.status_code = 401
                logger.info("Linear webhook rejected: %s", verification.outcome.value)
                return {"status": "unauthorized", "reason": verification.outcome.value}

            event = _parse_event(payload)
            if event is None:
                return {"status": "ignored"}

            ticket = await self._tickets.find_by_linear_issue_id(event.issue_id)
            if ticket is None:
                # Not one of ours (or not yet mirrored) — nothing to do.
                return {"status": "ignored-unmapped"}

            await self._inbound.handle(ticket, event)
            return {"status": "accepted"}

        return router

    def register_routes(self, app: FastAPI) -> None:
        app.include_router(self.build_router())


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None


def _parse_event(payload: dict[str, Any]) -> LinearInboundEvent | None:
    """Normalise a Linear webhook body, or None if it carries nothing we act on."""
    entity_type = str(payload.get("type") or "")
    data = payload.get("data") or {}
    actor = payload.get("actor") or {}
    actor_id = actor.get("id")
    actor_name = actor.get("name")

    if entity_type == "Issue":
        issue_id = data.get("id")
        if not issue_id:
            return None
        state = data.get("state") or {}
        new_state = linear_state_type_to_workflow_state(state.get("type"))
        return LinearInboundEvent(
            entity_type="Issue",
            actor_id=actor_id,
            actor_name=actor_name,
            issue_id=str(issue_id),
            new_state=new_state,
        )

    if entity_type == "Comment":
        issue = data.get("issue") or {}
        issue_id = issue.get("id")
        if not issue_id:
            return None
        return LinearInboundEvent(
            entity_type="Comment",
            actor_id=actor_id,
            actor_name=actor_name,
            issue_id=str(issue_id),
            comment_body=data.get("body"),
        )

    return None
