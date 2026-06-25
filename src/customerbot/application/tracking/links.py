"""Pure helpers for linking a ticket reference to its Slack source.

Board snapshots and SLA / digest alerts reference tickets by display id; with
no link, finding the ticket means scrolling or searching. These helpers turn a
`TIC-NNN` into a Slack mrkdwn link to the **ticket card** (the actionable hub,
which itself carries the original-thread link), falling back to the original
customer thread when a ticket has no card yet.

The archive-permalink format mirrors
`integration.slack.gateway.build_thread_link`; it's duplicated here (one line of
string formatting) so the application layer doesn't import the Slack adapter.
"""

from __future__ import annotations

from customerbot.domain.tickets.entities import Ticket


def _archive_link(workspace_url: str, channel_id: str, message_ts: str) -> str:
    return f"{workspace_url.rstrip('/')}/archives/{channel_id}/p{message_ts.replace('.', '')}"


def ticket_source_link(ticket: Ticket, workspace_url: str) -> str | None:
    """Best link to where the ticket lives: its card, else the original thread."""
    if ticket.card_channel_id and ticket.card_message_ts:
        return _archive_link(workspace_url, ticket.card_channel_id, ticket.card_message_ts)
    return ticket.original_slack_link


def linked_display_id(ticket: Ticket, workspace_url: str) -> str:
    """`<link|TIC-NNN>` when a source link exists, else plain `TIC-NNN`."""
    link = ticket_source_link(ticket, workspace_url)
    return f"<{link}|{ticket.display_id}>" if link else ticket.display_id
