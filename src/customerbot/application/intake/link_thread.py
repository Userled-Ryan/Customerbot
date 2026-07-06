"""Manual "Link to existing ticket" message shortcut (#userled-support).

When the same issue surfaces in a second support thread, the SE doesn't have to
re-log it — they right-click the message, pick the live ticket it belongs to,
and the thread is attached + marked in flight (🎫). On resolve it then gets the
"resolved" reply + ✅ alongside every other attached thread.

A thread already attached to a *different* ticket is reassigned (the "move");
the open modal surfaces that so submitting is the SE's explicit confirmation.

The Slack `views.open` dependency is injected as a view-builder callable so this
module stays free of the integration layer (same pattern as add-affected-org).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from customerbot.application.intake.support_threads import attach_and_react
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)

# `view_builder(ticket_options, *, private_metadata, current_note) -> view JSON`.
LinkViewBuilder = Callable[..., dict[str, Any]]

# Cap the dropdown well under Slack's 100-option limit.
_MAX_OPTIONS = 100


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def encode_metadata(channel_id: str, thread_ts: str) -> str:
    return f"{channel_id}|{thread_ts}"


def decode_metadata(raw: str) -> tuple[str, str] | None:
    channel_id, _, thread_ts = raw.partition("|")
    if not channel_id or not thread_ts:
        return None
    return channel_id, thread_ts


class OpenLinkModal:
    """Open the "link this thread to a ticket" picker from the message shortcut."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        orgs: OrgRepositoryPort,
        view_builder: LinkViewBuilder,
        support_channel_id: str | None,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._orgs = orgs
        self._view_builder = view_builder
        self._support_channel_id = support_channel_id

    async def execute(
        self, *, trigger_id: str, channel_id: str, thread_ts: str, invoker_user_id: str
    ) -> str | None:
        if not self._support_channel_id or channel_id != self._support_channel_id:
            await self._slack.send_ephemeral(
                channel_id,
                invoker_user_id,
                ":information_source: Linking a thread to a ticket is only "
                "available in #userled-support.",
            )
            return None

        live = await self._tickets.query_live()
        # Most-recent first, capped.
        options: list[tuple[int, str]] = []
        for ticket in reversed(live[-_MAX_OPTIONS:]):
            if ticket.id is None:
                continue
            options.append((ticket.id, await self._option_label(ticket)))

        current_note = await self._current_link_note(channel_id, thread_ts)
        view = self._view_builder(
            options,
            private_metadata=encode_metadata(channel_id, thread_ts),
            current_note=current_note,
        )
        return await self._slack.open_view(trigger_id, view)

    async def _option_label(self, ticket: Ticket) -> str:
        orgs_text = await self._org_names(ticket.id) if ticket.id is not None else ""
        suffix = f" · {orgs_text}" if orgs_text else ""
        return f"{ticket.display_id} · {ticket.title}{suffix}"

    async def _org_names(self, ticket_id: int) -> str:
        names: list[str] = []
        for org_id in await self._tickets.list_orgs(ticket_id):
            org = await self._orgs.get(org_id)
            names.append(org.name if org else org_id)
        return ", ".join(names)

    async def _current_link_note(self, channel_id: str, thread_ts: str) -> str | None:
        existing_id = await self._tickets.find_ticket_id_by_support_thread(channel_id, thread_ts)
        if existing_id is None:
            return None
        existing = await self._tickets.get(existing_id)
        if existing is None:
            return None
        orgs_text = await self._org_names(existing_id)
        where = f" ({orgs_text})" if orgs_text else ""
        return (
            f":warning: This thread is currently linked to *{existing.display_id}*{where}. "
            "Picking a different ticket will *move* it."
        )


class SubmitLinkThread:
    """Handle the link-ticket modal submission: attach the thread + 🎫."""

    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
    ) -> None:
        self._slack = slack
        self._tickets = tickets

    async def execute(
        self, *, channel_id: str, thread_ts: str, target_ticket_id: int, by_user_id: str
    ) -> Ticket | None:
        ticket = await self._tickets.get(target_ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Link-thread submitted for missing ticket %s", target_ticket_id)
            return None
        # link_support_thread reassigns on conflict, so this is also the "move".
        await attach_and_react(
            self._tickets,
            self._slack,
            ticket.id,
            channel_id,
            thread_ts,
            by_user_id=by_user_id,
            now=_utcnow(),
        )
        await self._slack.send_ephemeral(
            channel_id,
            by_user_id,
            f":ticket: Linked this thread to *{ticket.display_id}* — it's now marked in flight.",
        )
        return ticket
