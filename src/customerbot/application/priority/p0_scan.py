"""P0 candidate flag scheduled scan (flow §7c, min-spec §7c).

Every 30 minutes, look for tickets on critical-path features that are
affecting ≥5 orgs within the last 6 hours. DM SE + CTO with `[Set P0]`
buttons — never auto-set.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from customerbot.application.priority.actions import (
    ACTION_DISMISS_PRIO_DM,
    ACTION_SET_PRIORITY,
    REASON_P0_CANDIDATE,
    PriorityChangePayload,
)
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import LIVE_STATUSES, Priority

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=6)
ORG_THRESHOLD = 5


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class P0CandidateScan:
    def __init__(
        self,
        tickets: TicketRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        cto_user_id: str | None,
        critical_path_features: list[str],
    ) -> None:
        self._tickets = tickets
        self._slack = slack
        self._se_user_id = se_user_id
        self._cto_user_id = cto_user_id
        self._critical_path_features = {f.lower() for f in critical_path_features}
        self._already_flagged: set[int] = set()

    async def execute(self, *, now: datetime | None = None) -> list[int]:
        """Return ticket ids that triggered a P0 candidate DM this run."""
        if not self._critical_path_features:
            return []
        when = now or _utcnow()
        cutoff = when - WINDOW
        live = await self._tickets.query_live()
        triggered: list[int] = []
        for ticket in live:
            if ticket.id is None or ticket.id in self._already_flagged:
                continue
            if ticket.status not in LIVE_STATUSES:
                continue
            if ticket.priority == Priority.P0:
                continue
            if ticket.feature is None:
                continue
            if ticket.feature.lower() not in self._critical_path_features:
                continue
            if ticket.created_at < cutoff:
                # Outside the 6h window — only counts for fresh clusters.
                continue
            org_count = len(await self._tickets.list_orgs(ticket.id))
            if org_count < ORG_THRESHOLD:
                continue
            await self._dm_candidate(ticket, org_count)
            self._already_flagged.add(ticket.id)
            triggered.append(ticket.id)
        return triggered

    async def _dm_candidate(self, ticket: Ticket, org_count: int) -> None:
        blocks = _candidate_blocks(ticket, org_count)
        await self._slack.send_dm_blocks(
            self._se_user_id,
            blocks,
            text=f"P0 candidate: {ticket.display_id}",
        )
        if self._cto_user_id and self._cto_user_id != self._se_user_id:
            await self._slack.send_dm_blocks(
                self._cto_user_id,
                blocks,
                text=f"P0 candidate: {ticket.display_id}",
            )

    async def run_loop(self, interval_seconds: int = 1800) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("P0 candidate scan loop error")
            await asyncio.sleep(interval_seconds)


def _candidate_blocks(ticket: Ticket, org_count: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":rotating_light: *P0 candidate* — {org_count} orgs hit "
                    f"*{ticket.display_id}* "
                    f"(_{ticket.title}_) on critical-path feature "
                    f"*{ticket.feature}* within 6h.\n"
                    f"Currently {ticket.priority.value}. Confirm P0?"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Set P0"},
                    "action_id": ACTION_SET_PRIORITY,
                    "value": PriorityChangePayload(
                        ticket_id=ticket.id or 0,
                        priority=Priority.P0,
                        reason=REASON_P0_CANDIDATE,
                    ).encode(),
                    "style": "danger",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Skip"},
                    "action_id": ACTION_DISMISS_PRIO_DM,
                    "value": "skip",
                },
            ],
        },
    ]
