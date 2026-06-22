"""POST /webhooks/linear — signature gate + routing into the inbound handler."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.linear.inbound import LinearInboundHandler
from customerbot.application.linear.sync import LinearSync
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.resolve import ResolveTicket
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.linear.signing import expected_signature
from customerbot.integration.linear.webhook import LinearWebhook
from tests.conftest import FakeLinearPort, FakeSlackPort

SECRET = "whsec"


async def _setup(
    session_factory: async_sessionmaker[AsyncSession], *, secret: str | None = SECRET
) -> tuple[LinearWebhook, SQLiteTicketRepository, Ticket]:
    tickets = SQLiteTicketRepository(session_factory)
    events = SQLiteEventLogRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    slack = FakeSlackPort()
    fake_linear = FakeLinearPort()
    await orgs.upsert(Org(id="acme", name="Acme", csm_user_id="U_CSM"))
    created = await tickets.create(
        Ticket(
            title="checkout broken",
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            status=TicketStatus.IN_PROGRESS,
            lane=Lane.DEV_ACTION,
            severity=Severity.BLOCKING,
            reporter_user_id="U_SE",
            source=Source.CUSTOMER_CHANNEL,
            card_channel_id="C_SE_TICKETS",
            card_message_ts="1700000000.000100",
        )
    )
    assert created.id is not None
    await tickets.add_org(created.id, "acme")
    sync = LinearSync(linear=fake_linear, tickets=tickets, orgs=orgs)
    await sync.mirror_new_ticket(created)
    resolve = ResolveTicket(
        tickets=tickets, events=events, orgs=orgs, slack=slack, se_user_id="U_SE", linear=sync
    )
    drop = DropTicket(tickets=tickets, events=events, orgs=orgs, slack=slack, linear=sync)
    inbound = LinearInboundHandler(
        tickets=tickets,
        events=events,
        orgs=orgs,
        slack=slack,
        resolve_ticket=resolve,
        drop_ticket=drop,
        se_user_id="U_SE",
        actor_id="U_BOT",
    )
    webhook = LinearWebhook(inbound=inbound, tickets=tickets, webhook_secret=secret)
    refreshed = await tickets.get(created.id)
    assert refreshed is not None
    return webhook, tickets, refreshed


def _client(webhook: LinearWebhook) -> TestClient:
    app = FastAPI()
    webhook.register_routes(app)
    return TestClient(app)


def _signed(body: dict) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    return raw, expected_signature(SECRET, raw)


@pytest.mark.asyncio
async def test_signed_done_transitions_ticket(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    webhook, tickets, ticket = await _setup(session_factory)
    body = {
        "action": "update",
        "type": "Issue",
        "actor": {"id": "U_DEV", "name": "Dana"},
        "data": {"id": ticket.linear_issue_id, "state": {"type": "completed"}},
    }
    raw, sig = _signed(body)
    resp = _client(webhook).post(
        "/webhooks/linear", content=raw, headers={"Linear-Signature": sig}
    )
    assert resp.status_code == 202
    updated = await tickets.get(ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.AWAITING_CUSTOMER


@pytest.mark.asyncio
async def test_bad_signature_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    webhook, tickets, ticket = await _setup(session_factory)
    raw = json.dumps({"type": "Issue", "data": {"id": ticket.linear_issue_id}}).encode()
    resp = _client(webhook).post(
        "/webhooks/linear", content=raw, headers={"Linear-Signature": "deadbeef"}
    )
    assert resp.status_code == 401
    # Status untouched.
    updated = await tickets.get(ticket.id or 0)
    assert updated is not None and updated.status == TicketStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_unconfigured_secret_fails_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    webhook, _tickets, ticket = await _setup(session_factory, secret=None)
    raw, sig = _signed({"type": "Issue", "data": {"id": ticket.linear_issue_id}})
    resp = _client(webhook).post(
        "/webhooks/linear", content=raw, headers={"Linear-Signature": sig}
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_unmapped_issue_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    webhook, _tickets, _ticket = await _setup(session_factory)
    body = {
        "action": "update",
        "type": "Issue",
        "actor": {"id": "U_DEV"},
        "data": {"id": "lin_unknown", "state": {"type": "completed"}},
    }
    raw, sig = _signed(body)
    resp = _client(webhook).post(
        "/webhooks/linear", content=raw, headers={"Linear-Signature": sig}
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "ignored-unmapped"
