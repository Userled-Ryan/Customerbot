"""Auto-close + CSM pre-auto-close nudges for awaiting tickets (plan Chunk 8).

Daily job that walks every ticket in `Awaiting customer confirmation`:

- If it entered awaiting more than `auto_close_days` (default 7) ago,
  transition it to `Closed`, append the status-change + a comms-log entry,
  update the ticket card, and DM SE the §9e auto-close note. The proper
  customer-facing draft template lands in Chunk 11; for v1 the DM is a
  prompt asking SE to follow up if they want.
- Otherwise, fire the §9d pre-close nudge to the affected orgs' CSMs at
  the 7-day / 72-hour / 24-hour marks, once each. Throttle via the
  shared `sla_dm_state` table under `AWAITING_NUDGE_*` stages — presence
  of a row means "nudge sent".

Pre-close nudge schedule (relative to entry into awaiting):
  - day 0       → 7-day nudge (sent on the first scan after entry)
  - day 4       → 72-hour nudge
  - day 6       → 24-hour nudge
  - day 7       → auto-close

Inputs come from the event log (`last_status_change_into`) so the "when
did this ticket enter awaiting?" answer survives schema migrations and
restarts without denormalising onto the tickets table.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from customerbot.application.intake import ticket_card
from customerbot.application.sla import messages
from customerbot.domain.bot_state.entities import SLAStage, SLAState
from customerbot.domain.bot_state.ports import SLADMStateRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import (
    CommsDirection,
    TicketStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_AUTO_CLOSE_DAYS = 7

# Days-into-awaiting at which to fire each pre-close nudge.
# The values are also the "days until auto-close" carried in the DM text
# (auto-close at day 7 ⇒ nudge at day 0 is "7 days until close" etc).
_NUDGE_SCHEDULE: tuple[tuple[SLAStage, int, int], ...] = (
    # (stage, day-fired, days-until-close in the message)
    (SLAStage.AWAITING_NUDGE_7D, 0, 7),
    (SLAStage.AWAITING_NUDGE_3D, 4, 3),
    (SLAStage.AWAITING_NUDGE_1D, 6, 1),
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AutoCloseAwaiting:
    """Scheduled job — daily by default. Closes overdue awaiting tickets
    and fires the CSM pre-close nudges."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        sla_state: SLADMStateRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
        auto_close_days: int = DEFAULT_AUTO_CLOSE_DAYS,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._sla_state = sla_state
        self._slack = slack
        self._se_user_id = se_user_id
        self._auto_close_days = auto_close_days

    async def execute(
        self, *, now: datetime | None = None
    ) -> tuple[list[int], list[tuple[int, SLAStage]]]:
        """Run one pass. Returns (closed_ticket_ids, fired_nudges)."""
        when = now or _utcnow()
        closed: list[int] = []
        fired_nudges: list[tuple[int, SLAStage]] = []
        live = await self._tickets.query_live()
        for ticket in live:
            if ticket.id is None:
                continue
            if ticket.status != TicketStatus.AWAITING_CUSTOMER:
                continue
            entered_at = await self._events.last_status_change_into(
                ticket.id, TicketStatus.AWAITING_CUSTOMER
            )
            if entered_at is None:
                # Defensive: status says awaiting but no transition event. Skip
                # rather than guess; the next scan after a real transition will
                # catch up. Logged so we'd notice if it became persistent.
                logger.warning(
                    "Ticket %d in awaiting with no transition event; skipping",
                    ticket.id,
                )
                continue
            awaiting_days = (when - entered_at).days
            if awaiting_days >= self._auto_close_days:
                await self._auto_close(ticket, entered_at, when)
                closed.append(ticket.id)
                continue
            for stage, fire_day, days_until_close in _NUDGE_SCHEDULE:
                if awaiting_days < fire_day:
                    continue
                already_sent = await self._sla_state.get(ticket.id, stage)
                if already_sent is not None:
                    continue
                await self._nudge_csms(ticket, days_until_close, when)
                await self._sla_state.upsert(ticket.id, stage, SLAState.RED, when, now=when)
                fired_nudges.append((ticket.id, stage))
        return closed, fired_nudges

    async def _auto_close(self, ticket: Ticket, entered_at: datetime, now: datetime) -> None:
        assert ticket.id is not None
        prior_status = ticket.status
        await self._tickets.update_status(ticket.id, TicketStatus.CLOSED, now=now)
        await self._events.append_status_change(
            ticket_id=ticket.id,
            from_status=prior_status,
            to_status=TicketStatus.CLOSED,
            by_user_id=None,
            at=now,
            note="auto-closed after 7d awaiting customer",
        )
        await self._events.append_comms(
            ticket_id=ticket.id,
            direction=CommsDirection.OUTBOUND,
            channel="bot",
            sender_user_id=None,
            message_link=None,
            at=now,
            note="auto-close-note",
        )
        # Refresh ticket for card render + DM.
        refreshed = await self._tickets.get(ticket.id) or ticket
        if refreshed.card_channel_id and refreshed.card_message_ts:
            org_ids = await self._tickets.list_orgs(refreshed.id or 0)
            org_names = await self._org_names(org_ids)
            csm_ids = await self._csm_user_ids(org_ids)
            blocks = ticket_card.build_blocks(refreshed, org_names, csm_ids)
            await self._slack.update_message(
                refreshed.card_channel_id,
                refreshed.card_message_ts,
                blocks,
                text=ticket_card.fallback_text(refreshed),
            )
        awaiting_days = max((now - entered_at).days, self._auto_close_days)
        await self._slack.send_dm_blocks(
            self._se_user_id,
            messages.auto_close_blocks(refreshed, awaiting_days),
            text=f"Auto-closed: {refreshed.display_id}",
        )

    async def _nudge_csms(self, ticket: Ticket, days_until_close: int, _now: datetime) -> None:
        assert ticket.id is not None
        org_ids = await self._tickets.list_orgs(ticket.id)
        org_names = await self._org_names(org_ids)
        # Resolve CSM per affected org; dedupe (one CSM may own multiple orgs).
        recipients: list[str] = []
        for org_id in org_ids:
            org = await self._orgs.get(org_id)
            if org is None or org.csm_user_id is None:
                continue
            if org.csm_user_id not in recipients:
                recipients.append(org.csm_user_id)
        if not recipients:
            # No CSM to nudge — DM SE as the fallback owner. Better than silent.
            recipients = [self._se_user_id]
        blocks = messages.csm_pre_close_blocks(ticket, days_until_close, org_names)
        text = f"{ticket.display_id} auto-closes in {days_until_close}d unless customer confirms"
        for user_id in recipients:
            await self._slack.send_dm_blocks(user_id, blocks, text=text)

    async def _org_names(self, org_ids: list[str]) -> list[str]:
        names: list[str] = []
        for org_id in org_ids:
            org = await self._orgs.get(org_id)
            names.append(org.name if org else org_id)
        return names

    async def _csm_user_ids(self, org_ids: list[str]) -> list[str]:
        ids: list[str] = []
        for org_id in org_ids:
            org = await self._orgs.get(org_id)
            if org is not None and org.csm_user_id:
                ids.append(org.csm_user_id)
        return ids

    async def run_loop(self, interval_seconds: int = 86400) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Auto-close loop error")
            await asyncio.sleep(interval_seconds)
