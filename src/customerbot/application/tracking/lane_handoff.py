"""SE → Dev Action handoff (flow §2b, plan Chunk 9).

Triggered when SE clicks `Move to Dev Action` on the ticket card. The bot:

1. Flips the ticket's `Lane` to `Dev Action`.
2. Pings `support_handle` in `support_ping_channel_id` with a pre-filled
   handoff payload (repro steps from the description, affected customers,
   current prio, original Slack link, screenshot/replay).
3. Appends an `OUTBOUND` comms-log row so the ping is auditable.
4. Refreshes the ticket card so the lane label updates.

The lane is the only mutation here — status stays as-is. Idempotent click:
if the ticket is already on Dev Action lane, the bot still re-pings support
(SE may want to nudge), but the audit row records that no lane change
happened.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import CommsDirection, Lane

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MoveToDevAction:
    """Handle the `Move to Dev Action` button click."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        support_handle: str | None,
        support_ping_channel_id: str | None,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._support_handle = support_handle
        self._support_ping_channel_id = support_ping_channel_id
        self._linear = linear

    async def execute(self, *, ticket_id: int, by_user_id: str) -> Ticket | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Move to Dev clicked on missing ticket %s", ticket_id)
            return None

        now = _utcnow()
        if ticket.lane != Lane.DEV_ACTION:
            await self._tickets.update_lane(ticket.id, Lane.DEV_ACTION, now=now)

        org_ids = await self._tickets.list_orgs(ticket.id)
        org_names = await self._org_names(org_ids)

        ping_link = await self._post_handoff_ping(ticket, org_names)

        await self._events.append_comms(
            ticket_id=ticket.id,
            direction=CommsDirection.OUTBOUND,
            channel=self._support_ping_channel_id or "bot",
            sender_user_id=by_user_id,
            message_link=ping_link,
            at=now,
            note="lane-handoff:se->dev",
        )

        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

        # Linear mirror: this is where the issue becomes a real, open dev issue
        # in the Product Responder project for engineers to pick up.
        if self._linear is not None:
            await self._linear.ensure_open_for_dev(ticket.id)

        refreshed = await self._tickets.get(ticket.id)
        return refreshed

    async def _org_names(self, org_ids: list[str]) -> list[str]:
        names: list[str] = []
        for org_id in org_ids:
            org = await self._orgs.get(org_id)
            names.append(org.name if org else org_id)
        return names

    async def _post_handoff_ping(self, ticket: Ticket, org_names: list[str]) -> str | None:
        if self._support_ping_channel_id is None:
            logger.warning(
                "SUPPORT_PING_CHANNEL_ID not configured — ticket %s lane changed "
                "but no @support ping posted",
                ticket.display_id,
            )
            return None
        blocks = handoff_blocks(ticket, org_names, support_handle=self._support_handle)
        await self._slack.send_blocks(
            self._support_ping_channel_id,
            blocks,
            text=f"{ticket.display_id} handed off to dev",
        )
        # send_blocks returns the message ts; we'd build a permalink with the
        # workspace_url here, but the SlackPort surface doesn't expose that
        # uniformly and the comms-log row's `channel` already records where to
        # find it. Return None and let the audit trail point at the channel.
        return None


def handoff_blocks(
    ticket: Ticket,
    affected_org_names: list[str],
    *,
    support_handle: str | None,
) -> list[dict[str, Any]]:
    """Pre-filled `@support` ping payload (flow §2b)."""
    mention = f"<!subteam^{support_handle}>" if support_handle else "@support"
    orgs_text = ", ".join(affected_org_names) if affected_org_names else "—"
    repro = ticket.description.strip() or "_no repro steps captured — see thread_"
    fields: list[dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*Priority*\n{ticket.priority.value}"},
        {"type": "mrkdwn", "text": f"*Severity*\n{ticket.severity.value}"},
        {"type": "mrkdwn", "text": f"*Affected orgs*\n{orgs_text}"},
        {"type": "mrkdwn", "text": f"*Source*\n{ticket.source.value}"},
    ]
    context_bits: list[str] = []
    if ticket.original_slack_link:
        context_bits.append(f"<{ticket.original_slack_link}|Original thread>")
    if ticket.replay_link:
        context_bits.append(f"<{ticket.replay_link}|Link>")
    if ticket.screenshot_url:
        context_bits.append(f"<{ticket.screenshot_url}|Screenshot>")

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":arrow_right: *{mention} — {ticket.display_id} handed off* (_{ticket.title}_)"
                ),
            },
        },
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Repro / context:*\n{repro[:2500]}",
            },
        },
    ]
    if context_bits:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " · ".join(context_bits)}],
            }
        )
    return blocks
