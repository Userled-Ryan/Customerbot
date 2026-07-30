"""Thread reactions + replies for the threads a ticket was raised from.

Two kinds of channel join the 🎫→✅ status loop, and they differ in how loudly:

* **Internal** — `#userled-support` and the Gleap in-app channel
  (`Settings.support_thread_channel_ids`). The thread gets 🎫 while the ticket
  is in flight and ✅ + a short reply on resolve. Nothing else is said.
* **Customer channels** — any channel mapped to an org row
  (`orgs.slack_channel_id`). Same 🎫→✅ loop, *plus* a customer-facing
  acknowledgement when the ticket is logged ("we're on it, here's the number")
  and a follow-up when it's handed to engineering. This is the only place the
  bot speaks to a customer, and it only ever posts these short status lines.

A ticket can have several attached threads (raised by different people, or
merged in via dedupe), so resolve fans the reply + swap out across all of them.

These helpers are the single home for the reaction names, the reply copy, and
the "which threads belong to this ticket" logic — imported by the creation,
merge, manual-link, hand-off, and resolve use cases so they stay in lockstep.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import datetime

from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)

# Reaction names are passed to the Slack API without surrounding colons.
IN_FLIGHT_REACTION = "ticket"  # 🎫 — logged, being worked
RESOLVED_REACTION = "white_check_mark"  # ✅ — resolved (matches the card header)

RESOLVED_THREAD_REPLY = (
    ":white_check_mark: This has been marked as *resolved*. "
    "If you're still seeing the issue, just reply here and we'll take another look."
)


def logged_thread_reply(display_id: str) -> str:
    """Customer-facing acknowledgement posted in the thread a ticket was raised
    from. Deliberately short: it exists so the customer can see the report was
    picked up and has a reference, not to replace the SE's own reply."""
    return (
        f":eyes: Thanks — logged as *{display_id}*. "
        "The team is taking a look and we'll update you here."
    )


def parse_support(
    slack: SlackPort, link: str | None, support_channel_ids: Collection[str]
) -> tuple[str, str] | None:
    """`(channel_id, thread_ts)` for a thread link, but only when it points at
    one of the *internal* status-loop channels (#userled-support or the Gleap
    channel). None otherwise (customer channel, `/log`, malformed)."""
    if not link or not support_channel_ids:
        return None
    parsed = slack.parse_thread_link(link)
    if parsed is None or parsed[0] not in support_channel_ids:
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


async def attach_source_thread(
    tickets: TicketRepositoryPort,
    slack: SlackPort,
    orgs: OrgRepositoryPort,
    *,
    ticket_id: int,
    display_id: str,
    link: str | None,
    support_channel_ids: Collection[str],
    by_user_id: str | None,
    now: datetime,
) -> tuple[str, str] | None:
    """Attach the thread a ticket was raised (or merged in) from, and — when
    that thread lives in a customer channel — acknowledge it in-thread.

    Returns the `(channel_id, thread_ts)` that was attached, or None when the
    link is missing/unparseable or points at a channel we don't speak in.

    Internal channels are checked first so an org row mapped onto one could
    never turn it into a customer-facing channel.
    """
    if not link:
        return None
    parsed = slack.parse_thread_link(link)
    if parsed is None:
        return None
    channel_id, thread_ts = parsed

    if channel_id in support_channel_ids:
        await attach_and_react(
            tickets, slack, ticket_id, channel_id, thread_ts, by_user_id=by_user_id, now=now
        )
        return parsed

    org = await orgs.find_by_slack_channel(channel_id)
    if org is None:
        # Neither an internal status-loop channel nor a known customer channel —
        # leave it completely untouched.
        return None

    # Whether this exact thread is already on a ticket decides if the customer
    # has already been acknowledged. `link_support_thread` upserts and returns
    # nothing, so it can't tell us insert from update — read first.
    already = await tickets.find_ticket_id_by_support_thread(channel_id, thread_ts)

    # Row + 🎫 before the reply: if the post fails, the status loop is still
    # wired so resolve can close it.
    await attach_and_react(
        tickets, slack, ticket_id, channel_id, thread_ts, by_user_id=by_user_id, now=now
    )

    if already == ticket_id:
        logger.info(
            "Thread %s/%s already attached to ticket %s — skipping duplicate ack",
            channel_id,
            thread_ts,
            ticket_id,
        )
        return parsed
    if already is not None:
        logger.warning(
            "Thread %s/%s moved from ticket %s to %s — the old ticket no longer "
            "has a customer thread to close",
            channel_id,
            thread_ts,
            already,
            ticket_id,
        )
    await slack.send_message(channel_id, logged_thread_reply(display_id), thread_ts=thread_ts)
    return parsed


async def collect_threads(
    tickets: TicketRepositoryPort,
    slack: SlackPort,
    ticket: Ticket,
    support_channel_ids: Collection[str],
) -> list[tuple[str, str]]:
    """Every thread attached to a ticket, deduped — internal and customer alike.

    Stored rows are trusted: they're only ever written by the gated attach
    points, so anything in the table is a channel we've already spoken in.
    Falls back to the ticket's single `original_slack_link` when there are no
    stored rows — that covers tickets created before this feature existed, and
    stays internal-only on purpose (an old customer ticket was never
    acknowledged, so it shouldn't suddenly get a resolve reply).
    """
    if ticket.id is None:
        return []
    threads: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for channel_id, thread_ts in await tickets.list_support_threads(ticket.id):
        key = (channel_id, thread_ts)
        if key not in seen:
            seen.add(key)
            threads.append(key)
    if not threads:
        legacy = parse_support(slack, ticket.original_slack_link, support_channel_ids)
        if legacy is not None:
            threads.append(legacy)
    return threads


async def customer_threads(
    tickets: TicketRepositoryPort,
    orgs: OrgRepositoryPort,
    ticket: Ticket,
) -> list[tuple[str, str]]:
    """The attached threads that live in a customer channel — the only ones the
    bot posts customer-facing copy into. Internal support threads are excluded.

    No `original_slack_link` fallback: a ticket with no stored row was never
    acknowledged in-thread, so it shouldn't start getting follow-ups now.
    """
    if ticket.id is None:
        return []
    threads: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for channel_id, thread_ts in await tickets.list_support_threads(ticket.id):
        key = (channel_id, thread_ts)
        if key in seen:
            continue
        seen.add(key)
        if await orgs.find_by_slack_channel(channel_id) is not None:
            threads.append(key)
    return threads
