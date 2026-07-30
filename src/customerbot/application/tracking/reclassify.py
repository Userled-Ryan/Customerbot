"""Reclassification — auto-notifies internal stakeholders (plan Chunk 10).

Two use cases compose the flow:

- `OpenReclassifyModal` — opens the §4c modal from the ticket-card button.
- `SubmitReclassify` — on `view_submission`:
    1. UPDATE the ticket's type + subtype.
    2. INSERT `event_reclassifications` (audit trail).
    3. Refresh the ticket card (type label changed).
    4. Resolve internal stakeholders (CSM owner, original reporter, the
       new owner, `@support` if the ticket has been handed off to dev).
    5. Immediately DM each user-recipient / post to each channel-recipient a
       plain "ticket reclassified X → Y" notice — no draft, no review step.
    6. INSERT an `OUTBOUND` `event_comms_log` row per recipient so the audit
       trail records exactly where the notice went.

The bot **never** notifies customers — by construction, the recipient list is
built from internal users (DM channels start with `D…`) and internal Slack
channels passed via config. The customer thread link is included in the notice
so SE / `@support` can navigate there if they need to, but no message is ever
posted to that thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from customerbot.application.intake.submissions import ReclassifySubmission
from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.linear.sync import LinearSync
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


# Friendly type names for the stakeholder notice (kept here rather than
# reaching into the Slack/Linear adapters — the application layer owns its copy).
_TYPE_LABEL: dict[TicketType, str] = {
    TicketType.BUG: "Bug",
    TicketType.CONFIG: "Config",
    TicketType.FAQ: "FAQ",
    TicketType.FEATURE_REQUEST: "Product change",
    TicketType.CSM_HELP: "CSM Help Request",
}


def _type_label(ticket_type: TicketType) -> str:
    return _TYPE_LABEL.get(ticket_type, ticket_type.value)


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


class SubmitReclassify:
    """Handle the `reclassify` modal `view_submission`.

    Updates the ticket, writes the audit row, refreshes the card, syncs the
    Linear type label, then immediately notifies the internal stakeholders.
    """

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        support_handle: str | None,
        support_ping_channel_id: str | None,
        linear: LinearSync | None = None,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._support_handle = support_handle
        self._support_ping_channel_id = support_ping_channel_id
        self._linear = linear

    async def execute(
        self,
        submission: ReclassifySubmission,
        *,
        by_user_id: str,
    ) -> int:
        """Return the count of stakeholders actually notified."""
        ticket = await self._tickets.get(submission.ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Reclassify submitted for missing ticket %s", submission.ticket_id)
            return 0

        from_type = ticket.type
        from_subtype = ticket.subtype
        if from_type == submission.new_type and from_subtype == submission.new_subtype:
            logger.info(
                "Reclassify no-op: %s already %s/%s",
                ticket.display_id,
                from_type.value,
                from_subtype.value,
            )
            return 0

        now = _utcnow()
        await self._tickets.update_type_subtype(
            ticket.id, submission.new_type, submission.new_subtype, now=now
        )
        await self._events.append_reclassification(
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

        # Keep the Linear mirror's type label in step with the new type so
        # reports stay filterable. Best-effort; only swaps on a type change.
        if self._linear is not None and from_type != submission.new_type:
            await self._linear.sync_type_label(
                ticket.id, from_type=from_type, to_type=submission.new_type
            )

        refreshed = await self._tickets.get(ticket.id) or ticket
        recipients = await self._resolve_recipients(refreshed, submission=submission)
        body = notice_text(
            ticket=refreshed,
            submission=submission,
            from_type=from_type,
            from_subtype=from_subtype,
        )
        return await self._notify(refreshed, recipients, body, by_user_id=by_user_id, now=now)

    async def _notify(
        self,
        ticket: Ticket,
        recipients: list[dict[str, str]],
        body: str,
        *,
        by_user_id: str,
        now: datetime,
    ) -> int:
        assert ticket.id is not None
        sent_count = 0
        for recipient in recipients:
            kind = recipient.get("kind")
            ident = recipient.get("id")
            if not kind or not ident:
                continue
            channel_label: str
            if kind == "user":
                # send_dm_blocks routes via the user's DM channel.
                await self._slack.send_dm_blocks(
                    ident,
                    alert_blocks(ticket, body),
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
                note="reclassify-notice",
            )
            sent_count += 1
        logger.info(
            "Reclassify notice for %s sent to %d recipient(s)", ticket.display_id, sent_count
        )
        return sent_count

    async def _resolve_recipients(
        self,
        ticket: Ticket,
        *,
        submission: ReclassifySubmission,
    ) -> list[dict[str, str]]:
        """Return the deduped recipients list as `{kind, id}` dicts.

        `kind` is "user" (DM the user) or "channel" (post to the channel).
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


# --- Rendering helpers (pure) -------------------------------------------------


def notice_text(
    *,
    ticket: Ticket,
    submission: ReclassifySubmission,
    from_type: TicketType,
    from_subtype: TicketSubtype,
) -> str:
    """§9f internal notice template — auto-sent to stakeholders on reclassify."""
    thread_line = (
        f"Customer thread: {ticket.original_slack_link}"
        if ticket.original_slack_link
        else "Customer thread: _(none linked)_"
    )
    return (
        f"*Reclassified:* {_type_label(from_type)} → {_type_label(submission.new_type)} "
        f"({from_subtype.value} → {submission.new_subtype.value})\n"
        f"*Why:* {submission.reason}\n"
        f"*Next step:* {submission.next_step}\n"
        f"*Owner:* <@{submission.owner_user_id}>\n\n"
        f"{thread_line}"
    )


def alert_blocks(ticket: Ticket, body: str) -> list[dict[str, Any]]:
    """The §9f notice posted to internal stakeholders."""
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
