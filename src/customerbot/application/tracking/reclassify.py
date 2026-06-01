"""Reclassification — drafted internally, never auto-sent (plan Chunk 10).

Three use cases compose the flow:

- `OpenReclassifyModal` — opens the §4c modal from the ticket-card button.
- `SubmitReclassifyDraft` — on `view_submission`:
    1. UPDATE the ticket's type + subtype.
    2. INSERT `event_reclassifications` (audit trail).
    3. Refresh the ticket card (type label changed).
    4. Resolve internal stakeholders (CSM owner, original reporter, the
       new owner, `@support` if the ticket has been handed off to dev).
    5. INSERT `pending_reclassify_sends` carrying the §9f draft + recipients.
    6. DM SE the draft with `Send to stakeholders` / `Cancel` buttons.
- `SendReclassifyAlert` — on SE clicking `Send`:
    1. Load the pending row.
    2. DM each user-recipient; post a channel message for each channel-recipient.
    3. INSERT an `OUTBOUND` `event_comms_log` row per recipient so the
       audit trail records exactly where the alert went.
    4. Delete the pending row.
- `DismissReclassifyDraft` — on `Cancel`: just delete the pending row.

The bot **never** sends to customers — by construction, the recipient
list is built from internal users (DM channels start with `D…`) and
internal Slack channels passed via config. The customer thread link is
included in the draft so SE / `@support` can navigate there if they need
to, but no message is ever posted to that thread.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from customerbot.application.intake.submissions import ReclassifySubmission
from customerbot.application.intake.ticket_card import refresh_card
from customerbot.domain.bot_state.entities import PendingReclassifySend
from customerbot.domain.bot_state.ports import PendingReclassifySendRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import (
    CommsDirection,
    Lane,
    TicketSubtype,
    TicketType,
)

logger = logging.getLogger(__name__)

PENDING_TTL = timedelta(days=7)

ACTION_SEND_RECLASSIFY = "reclassify_send"
ACTION_DISMISS_RECLASSIFY = "reclassify_dismiss"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# `view_builder(ticket_id, current_type, current_subtype) -> view JSON`.
ReclassifyViewBuilder = Callable[..., dict[str, Any]]


class OpenReclassifyModal:
    """Open the reclassify modal in response to the `Reclassify` click."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        view_builder: ReclassifyViewBuilder,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._view_builder = view_builder

    async def execute(self, *, trigger_id: str, ticket_id: int) -> str | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None:
            logger.warning("Reclassify clicked on missing ticket %s", ticket_id)
            return None
        view = self._view_builder(
            ticket_id=ticket_id,
            current_type=ticket.type,
            current_subtype=ticket.subtype,
        )
        return await self._slack.open_view(trigger_id, view)


class SubmitReclassifyDraft:
    """Handle the `reclassify` modal `view_submission`."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        pending: PendingReclassifySendRepositoryPort,
        se_user_id: str,
        support_handle: str | None,
        support_ping_channel_id: str | None,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._pending = pending
        self._se_user_id = se_user_id
        self._support_handle = support_handle
        self._support_ping_channel_id = support_ping_channel_id

    async def execute(
        self,
        submission: ReclassifySubmission,
        *,
        by_user_id: str,
    ) -> PendingReclassifySend | None:
        ticket = await self._tickets.get(submission.ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Reclassify submitted for missing ticket %s", submission.ticket_id)
            return None

        from_type = ticket.type
        from_subtype = ticket.subtype
        if from_type == submission.new_type and from_subtype == submission.new_subtype:
            logger.info(
                "Reclassify no-op: %s already %s/%s",
                ticket.display_id,
                from_type.value,
                from_subtype.value,
            )
            return None

        now = _utcnow()
        await self._tickets.update_type_subtype(
            ticket.id, submission.new_type, submission.new_subtype, now=now
        )
        event_id = await self._events.append_reclassification(
            ticket_id=ticket.id,
            from_type=from_type,
            to_type=submission.new_type,
            from_subtype=from_subtype,
            to_subtype=submission.new_subtype,
            by_user_id=by_user_id,
            at=now,
            reason=submission.reason,
            next_step=submission.next_step,
            owner_user_id=submission.owner_user_id,
        )

        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

        refreshed = await self._tickets.get(ticket.id) or ticket
        recipients = await self._resolve_recipients(refreshed, submission=submission)
        draft_text = render_alert_text(
            ticket=refreshed,
            submission=submission,
            from_type=from_type,
            from_subtype=from_subtype,
        )

        pending = await self._pending.create(
            PendingReclassifySend(
                ticket_id=ticket.id,
                reclassification_event_id=event_id,
                recipients_json=json.dumps(recipients),
                draft_text=draft_text,
                dm_channel_id="",
                dm_message_ts="",
                created_at=now,
                expires_at=now + PENDING_TTL,
            )
        )
        assert pending.id is not None

        blocks = draft_dm_blocks(
            pending_id=pending.id,
            ticket=refreshed,
            draft_text=draft_text,
            recipients=recipients,
        )
        sent = await self._slack.send_dm_blocks(
            self._se_user_id,
            blocks,
            text=f"Reclassify draft: {refreshed.display_id}",
        )
        if sent is not None:
            dm_channel, dm_ts = sent
            await self._pending.update_dm_metadata(pending.id, dm_channel, dm_ts)
            return PendingReclassifySend(
                id=pending.id,
                ticket_id=pending.ticket_id,
                reclassification_event_id=pending.reclassification_event_id,
                recipients_json=pending.recipients_json,
                draft_text=pending.draft_text,
                dm_channel_id=dm_channel,
                dm_message_ts=dm_ts,
                created_at=pending.created_at,
                expires_at=pending.expires_at,
            )
        return pending

    async def _resolve_recipients(
        self,
        ticket: Ticket,
        *,
        submission: ReclassifySubmission,
    ) -> list[dict[str, str]]:
        """Return the deduped recipients list as `{kind, id}` dicts.

        `kind` is "user" (DM the user) or "channel" (post to the channel).
        Order matters for the §9f alert body — we render the recipients in
        the draft message exactly as resolved here.
        """
        recipients: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: str, ident: str) -> None:
            if not ident:
                return
            key = (kind, ident)
            if key in seen:
                return
            seen.add(key)
            recipients.append({"kind": kind, "id": ident})

        # Original logger.
        add("user", ticket.reporter_user_id)
        # The owner the SE just assigned in the modal.
        add("user", submission.owner_user_id)
        # CSM of every affected org.
        assert ticket.id is not None
        for org_id in await self._tickets.list_orgs(ticket.id):
            org = await self._orgs.get(org_id)
            if org is not None and org.csm_user_id:
                add("user", org.csm_user_id)
        # @support if the ticket has been handed off to dev (lane is Dev Action
        # AND the support ping channel is configured). The plan says "if
        # involved"; v1 reads that as "support has already been pinged for this
        # ticket via the lane handoff" — i.e. lane == DEV_ACTION.
        if ticket.lane == Lane.DEV_ACTION and self._support_ping_channel_id is not None:
            add("channel", self._support_ping_channel_id)
        return recipients


class SendReclassifyAlert:
    """Handle the SE clicking `Send to stakeholders`."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        pending: PendingReclassifySendRepositoryPort,
        support_handle: str | None,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._events = events
        self._pending = pending
        self._support_handle = support_handle

    async def execute(self, *, pending_id: int, by_user_id: str) -> int:
        """Return the count of recipients actually messaged."""
        pending = await self._pending.get(pending_id)
        if pending is None:
            logger.warning("Send-reclassify clicked on missing pending row %s", pending_id)
            return 0
        ticket = await self._tickets.get(pending.ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning(
                "Send-reclassify pending %s references missing ticket %s",
                pending_id,
                pending.ticket_id,
            )
            await self._pending.delete(pending_id)
            return 0

        recipients = json.loads(pending.recipients_json)
        body = pending.draft_text
        now = _utcnow()
        sent_count = 0
        for recipient in recipients:
            kind = recipient.get("kind")
            ident = recipient.get("id")
            if not kind or not ident:
                continue
            blocks = alert_blocks(ticket, body)
            channel_label: str
            if kind == "user":
                # send_dm_blocks routes via the user's DM channel.
                await self._slack.send_dm_blocks(
                    ident,
                    blocks,
                    text=f"Reclassified: {ticket.display_id}",
                )
                channel_label = f"dm:{ident}"
            elif kind == "channel":
                channel_text = body
                if self._support_handle:
                    channel_text = f"<!subteam^{self._support_handle}> {body}"
                await self._slack.send_blocks(
                    ident,
                    alert_blocks(ticket, channel_text),
                    text=f"Reclassified: {ticket.display_id}",
                )
                channel_label = ident
            else:
                logger.warning("Unknown reclassify recipient kind %r", kind)
                continue
            await self._events.append_comms(
                ticket_id=ticket.id,
                direction=CommsDirection.OUTBOUND,
                channel=channel_label,
                sender_user_id=by_user_id,
                message_link=None,
                at=now,
                note="reclassify-alert",
            )
            sent_count += 1

        # Update the original DM to disable the buttons (best-effort).
        if pending.dm_channel_id and pending.dm_message_ts:
            await self._slack.update_message(
                pending.dm_channel_id,
                pending.dm_message_ts,
                sent_confirmation_blocks(ticket, recipient_count=sent_count),
                text=f"Reclassify alert sent ({sent_count} recipients)",
            )

        await self._pending.delete(pending_id)
        return sent_count


class DismissReclassifyDraft:
    """Handle `Cancel` — just delete the pending row."""

    def __init__(
        self,
        slack: SlackPort,
        pending: PendingReclassifySendRepositoryPort,
    ) -> None:
        self._slack = slack
        self._pending = pending

    async def execute(self, *, pending_id: int) -> None:
        pending = await self._pending.get(pending_id)
        if pending is None:
            return
        if pending.dm_channel_id and pending.dm_message_ts:
            await self._slack.update_message(
                pending.dm_channel_id,
                pending.dm_message_ts,
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":wastebasket: _Reclassify draft cancelled._",
                        },
                    }
                ],
                text="Reclassify draft cancelled",
            )
        await self._pending.delete(pending_id)


# --- Rendering helpers (pure) -------------------------------------------------


def render_alert_text(
    *,
    ticket: Ticket,
    submission: ReclassifySubmission,
    from_type: TicketType,
    from_subtype: TicketSubtype,
) -> str:
    """§9f internal alert template. Stored as draft text on the pending row
    so the eventual Send delivers exactly what SE reviewed.
    """
    thread_line = (
        f"Customer thread: {ticket.original_slack_link}"
        if ticket.original_slack_link
        else "Customer thread: _(none linked)_"
    )
    return (
        f"*Reclassified:* {from_type.value} → {submission.new_type.value} "
        f"({from_subtype.value} → {submission.new_subtype.value})\n"
        f"*Why:* {submission.reason}\n"
        f"*Next step:* {submission.next_step}\n"
        f"*Owner:* <@{submission.owner_user_id}>\n\n"
        f"{thread_line}"
    )


def draft_dm_blocks(
    *,
    pending_id: int,
    ticket: Ticket,
    draft_text: str,
    recipients: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if recipients:
        recipient_summary = ", ".join(_format_recipient(r) for r in recipients)
    else:
        recipient_summary = "_no stakeholders resolved — nothing will be sent_"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":writing_hand: *Reclassify draft* for {ticket.display_id} — "
                    f"review before sending."
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": draft_text},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"*Will go to:* {recipient_summary}",
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Send to stakeholders"},
                    "action_id": ACTION_SEND_RECLASSIFY,
                    "value": str(pending_id),
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "action_id": ACTION_DISMISS_RECLASSIFY,
                    "value": str(pending_id),
                },
            ],
        },
    ]


def alert_blocks(ticket: Ticket, body: str) -> list[dict[str, Any]]:
    """The actual §9f alert posted to internal stakeholders on Send."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":mag: *{ticket.display_id}* — _{ticket.title}_",
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
    ]


def sent_confirmation_blocks(ticket: Ticket, *, recipient_count: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: *Sent* — reclassify alert for "
                    f"{ticket.display_id} delivered to {recipient_count} "
                    f"stakeholder(s)."
                ),
            },
        },
    ]


def _format_recipient(recipient: dict[str, str]) -> str:
    kind = recipient.get("kind", "?")
    ident = recipient.get("id", "")
    if kind == "user":
        return f"<@{ident}>"
    if kind == "channel":
        return f"<#{ident}>"
    return f"{kind}:{ident}"
