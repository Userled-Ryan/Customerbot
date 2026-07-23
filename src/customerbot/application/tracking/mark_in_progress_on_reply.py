"""Auto-advance a ticket to `In progress` when its SE replies in the thread.

When a CSM logs a ticket from a customer channel thread, the ticket starts as
`New`. The moment the ticket's *assigned* SE owner posts a reply in that same
thread ("taking a look"), that's the real signal work has started — so we flip
the ticket to `In progress` and mirror the state onto Linear, capturing
`first_response_at` for SLA reporting along the way.

This is the Slack→Linear counterpart of the inbound
`LinearInboundHandler._reflect_in_progress` (Linear→Slack) path.

Deliberately narrow so it never fires spuriously:
- only the ticket's assigned `se_owner_user_id` triggers it (a CSM, customer or
  other SE reply is a no-op),
- only the thread the ticket was raised from counts — matched via the ticket's
  `original_slack_link`,
- only a `New` ticket advances; every other status (including the retired ones)
  is an idempotent no-op, so this never auto-reopens a resolved/closed ticket.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import UTC, datetime

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import TicketStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MarkInProgressOnReply:
    """Move a `New` ticket to `In progress` when its SE replies in the thread."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        se_member_ids: Collection[str],
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        # Curated SE candidate list — a cheap in-memory pre-filter so the vast
        # majority of channel messages (customers, CSMs) never hit the DB. The
        # authoritative check is the ticket's assigned owner, below.
        self._se_member_ids = set(se_member_ids)
        self._linear = linear

    async def execute(self, *, channel_id: str, thread_ts: str, sender_user_id: str) -> bool:
        """Return True if the ticket was advanced to In progress."""
        # 1. Cheap pre-gate: only an SE could ever be the assigned owner.
        if sender_user_id not in self._se_member_ids:
            return False

        # 2. Is this thread the one a ticket was raised from?
        permalink = self._slack.build_thread_link(channel_id, thread_ts)
        ticket = await self._tickets.find_by_slack_link(permalink)
        if ticket is None or ticket.id is None:
            return False

        # 3. Authoritative: only the *assigned* SE owner triggers the move.
        if ticket.se_owner_user_id != sender_user_id:
            return False

        # 4. Only advance a New ticket — never auto-reopen or re-fire.
        if ticket.status != TicketStatus.NEW:
            return False

        now = _utcnow()
        await self._tickets.update_status(ticket.id, TicketStatus.IN_PROGRESS, now=now)
        await self._events.append_status_change(
            ticket_id=ticket.id,
            from_status=TicketStatus.NEW,
            to_status=TicketStatus.IN_PROGRESS,
            by_user_id=sender_user_id,
            at=now,
            note="se-reply-in-thread",
        )
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
        # Keep the Linear mirror in lockstep. Best-effort — a Linear hiccup can't
        # undo the Slack-side transition.
        if self._linear is not None:
            await self._linear.sync_state(ticket.id)
        logger.info(
            "Advanced %s to In progress — SE %s replied in its thread",
            ticket.display_id,
            sender_user_id,
        )
        return True
