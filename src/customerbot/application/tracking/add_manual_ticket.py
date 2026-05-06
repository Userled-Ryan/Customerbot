from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs

from customerbot.domain.tracking.entities import TrackedConversation
from customerbot.domain.tracking.ports import ConversationRepositoryPort, MessengerPort

logger = logging.getLogger(__name__)

_SLACK_LINK_RE = re.compile(
    r"https?://[a-zA-Z0-9.\-]+\.slack\.com/archives/(?P<channel>[A-Z0-9]+)/p(?P<ts>\d+)"
    r"(?:\?(?P<query>[^\s>|]*))?"
)


@dataclass
class ManualTicketResult:
    ok: bool
    message: str


class AddManualTicket:
    """Use case: create a ticket from a Slack thread link DM'd to the bot."""

    def __init__(
        self,
        repo: ConversationRepositoryPort,
        messenger: MessengerPort,
    ) -> None:
        self._repo = repo
        self._messenger = messenger

    async def execute(self, text: str) -> ManualTicketResult:
        parsed = _parse_slack_link(text)
        if parsed is None:
            return ManualTicketResult(
                ok=False,
                message=(
                    "⚠️ I couldn't find a Slack thread link. "
                    "DM me a link like `https://workspace.slack.com/archives/C123/p1700000000123456` "
                    "to open a ticket for that thread."
                ),
            )
        channel_id, thread_ts = parsed
        if channel_id.startswith("D"):
            return ManualTicketResult(
                ok=False,
                message="⚠️ Can't track DM threads — only public/private channels.",
            )

        existing = await self._repo.find_by_thread(channel_id, thread_ts)
        if existing is not None:
            return ManualTicketResult(
                ok=False,
                message=f"ℹ️ Already tracked as `#{existing.ticket_number}` (in #{existing.channel_name or channel_id}).",
            )

        try:
            channel_name = await self._messenger.get_channel_name(channel_id)
            parent_text = await self._messenger.get_message_text(channel_id, thread_ts)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch metadata for manual ticket")
            return ManualTicketResult(
                ok=False,
                message=f"⚠️ Couldn't fetch the thread (am I in that channel?): {exc}",
            )

        context = (parent_text or "").strip()[:200]
        now = datetime.utcnow()
        conversation = TrackedConversation(
            channel_id=channel_id,
            thread_ts=thread_ts,
            channel_name=channel_name,
            category="manual",
            context=context,
            opened_at=now,
            last_ryan_reply_at=None,
        )
        await self._repo.upsert(conversation)
        created = await self._repo.find_by_thread(channel_id, thread_ts)
        ticket_id = created.ticket_number if created else None
        label = f"`#{ticket_id}`" if ticket_id else "ticket"
        logger.info("Manually opened conversation %s:%s", channel_id, thread_ts)
        return ManualTicketResult(
            ok=True,
            message=f"✅ Created {label} for thread in #{channel_name}.",
        )


def _parse_slack_link(text: str) -> tuple[str, str] | None:
    match = _SLACK_LINK_RE.search(text)
    if not match:
        return None
    channel_id = match.group("channel")
    raw_ts = match.group("ts")
    message_ts = _format_ts(raw_ts)

    query = match.group("query") or ""
    parsed_qs = parse_qs(query)
    thread_ts_values = parsed_qs.get("thread_ts")
    thread_ts = thread_ts_values[0] if thread_ts_values else message_ts
    return channel_id, thread_ts


def _format_ts(raw: str) -> str:
    if len(raw) <= 6:
        return raw
    return f"{raw[:-6]}.{raw[-6:]}"


__all__ = ["AddManualTicket", "ManualTicketResult"]
