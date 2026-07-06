"""Support-thread reactions + reply, shared across the intake/resolve flows.

A #userled-support thread attached to a ticket gets a 🎫 reaction while the
ticket is in flight; on resolve the bot posts a short reply and swaps 🎫→✅.
A ticket can have several attached threads (raised by different people, or
merged in via dedupe), so resolve fans the reply + swap out across all of them.

These helpers are the single home for the reaction names, the reply copy, and
the "which threads belong to this ticket" logic — imported by the creation,
merge, manual-link, and resolve use cases so they stay in lockstep.
"""

from __future__ import annotations

from datetime import datetime

from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort

# Reaction names are passed to the Slack API without surrounding colons.
IN_FLIGHT_REACTION = "ticket"  # 🎫 — logged, being worked
RESOLVED_REACTION = "white_check_mark"  # ✅ — resolved (matches the card header)

RESOLVED_THREAD_REPLY = (
    ":white_check_mark: This has been marked as *resolved*. "
    "If you're still seeing the issue, just reply here and we'll take another look."
)


def parse_support(
    slack: SlackPort, link: str | None, support_channel_id: str | None
) -> tuple[str, str] | None:
    """`(channel_id, thread_ts)` for a thread link, but only when it points at
    the support channel. None otherwise (other channel, `/log`, malformed)."""
    if not link or not support_channel_id:
        return None
    parsed = slack.parse_thread_link(link)
    if parsed is None or parsed[0] != support_channel_id:
        return None
    return parsed


async def attach_and_react(
    tickets: TicketRepositoryPort,
    slack: SlackPort,
    ticket_id: int,
    channel_id: str,
    thread_ts: str,
    *,
    by_user_id: str | None,
    now: datetime,
) -> None:
    """Record the thread on the ticket and mark it in flight (🎫)."""
    await tickets.link_support_thread(
        ticket_id, channel_id, thread_ts, by_user_id=by_user_id, now=now
    )
    await slack.add_reaction(channel_id, thread_ts, IN_FLIGHT_REACTION)


async def collect_threads(
    tickets: TicketRepositoryPort,
    slack: SlackPort,
    ticket: Ticket,
    support_channel_id: str | None,
) -> list[tuple[str, str]]:
    """Every support thread attached to a ticket, deduped.

    Falls back to the ticket's single `original_slack_link` when there are no
    stored rows — covers tickets created before this feature existed.
    """
    if support_channel_id is None or ticket.id is None:
        return []
    threads: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for channel_id, thread_ts in await tickets.list_support_threads(ticket.id):
        key = (channel_id, thread_ts)
        if channel_id == support_channel_id and key not in seen:
            seen.add(key)
            threads.append(key)
    if not threads:
        legacy = parse_support(slack, ticket.original_slack_link, support_channel_id)
        if legacy is not None:
            threads.append(legacy)
    return threads
