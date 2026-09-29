"""Sick / holiday mode — `/ooo`.

Marking an SE out stops new tickets landing on them without touching the Fly
secrets: whoever a ticket would be assigned to (the default SE owner, the
round-robin pick, or the urgent-ticket owner) is swapped for their cover — or,
with no cover set, for a round-robin pick over the rest of the pool. The swap
happens once, in `SubmitTicketForm.proceed_create_and_announce`, so every intake
path gets it. Optionally the SE's already-open tickets move too.

Absences live in the `se_availability` table and expire lazily: a row whose
`back_on` has arrived (SE-local date) is simply ignored.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from customerbot.application.intake.apply_se_owner import ApplySeOwnerChange
from customerbot.application.intake.se_owner_actions import SeOwnerChangePayload
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import SeAbsence
from customerbot.domain.tickets.ports import SeAvailabilityRepositoryPort, TicketRepositoryPort

logger = logging.getLogger(__name__)

# `exclude` -> the balanced round-robin pick over the SE pool minus `exclude`,
# or None when nobody is left. Bound to `SubmitTicketForm.pick_se_owner`.
PickOwner = Callable[[frozenset[str]], Awaitable[str | None]]


def _tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown SE timezone %r — falling back to UTC for /ooo", timezone_name)
        return ZoneInfo("UTC")


def format_day(d: date) -> str:
    """`Fri 3 Oct` — compact enough for a one-line notice."""
    return f"{d:%a} {d.day} {d:%b}"


def describe_absence(absence: SeAbsence) -> str:
    """One mrkdwn line: who's out, until when, and where their tickets go."""
    until = f" until {format_day(absence.back_on)}" if absence.back_on else ""
    cover = f"<@{absence.cover_user_id}>" if absence.cover_user_id else "the rest of the rotation"
    return f":palm_tree: <@{absence.user_id}> is out{until} — new tickets go to {cover}"


class SeAvailability:
    """Read side: who is out *today* (SE-local)."""

    def __init__(self, repo: SeAvailabilityRepositoryPort, *, se_timezone: str = "UTC") -> None:
        self._repo = repo
        self._tz = _tz(se_timezone)

    def today(self) -> date:
        return datetime.now(UTC).astimezone(self._tz).date()

    async def active(self) -> dict[str, SeAbsence]:
        today = self.today()
        return {a.user_id: a for a in await self._repo.list_all() if a.is_active(today)}


async def reroute_if_away(
    owner: str | None, away: dict[str, SeAbsence], pick_owner: PickOwner
) -> str | None:
    """Swap an out-of-office `owner` for their cover, else a round-robin pick
    over whoever isn't out. Keeps `owner` if they're in, or if everyone is out."""
    absence = away.get(owner) if owner else None
    if absence is None:
        return owner
    if absence.cover_user_id and absence.cover_user_id not in away:
        return absence.cover_user_id
    picked = await pick_owner(frozenset(away))
    if picked is None:
        logger.warning("Everyone in the SE pool is out — leaving ticket with %s", owner)
        return owner
    return picked


@dataclass(frozen=True)
class OooValidationError:
    field: str  # "back_on" | "cover"
    message: str


class MarkOoo:
    """Mark an SE out: record the absence, optionally move their open tickets,
    and announce it (#se-tickets notice + a DM to the cover)."""

    def __init__(
        self,
        *,
        repo: SeAvailabilityRepositoryPort,
        availability: SeAvailability,
        tickets: TicketRepositoryPort,
        apply_se_owner_change: ApplySeOwnerChange,
        slack: SlackPort,
        pick_owner: PickOwner,
        se_tickets_channel_id: str | None,
    ) -> None:
        self._repo = repo
        self._availability = availability
        self._tickets = tickets
        self._apply_se_owner_change = apply_se_owner_change
        self._slack = slack
        self._pick_owner = pick_owner
        self._se_tickets_channel_id = se_tickets_channel_id

    async def validate(
        self, *, user_id: str, cover_user_id: str | None, back_on: date | None
    ) -> list[OooValidationError]:
        errors: list[OooValidationError] = []
        if back_on is not None and back_on <= self._availability.today():
            errors.append(OooValidationError("back_on", "Pick a day after today."))
        if cover_user_id is not None:
            if cover_user_id == user_id:
                errors.append(
                    OooValidationError("cover", "The cover can't be the person who's out.")
                )
            elif cover_user_id in await self._availability.active():
                errors.append(OooValidationError("cover", "That person is marked out too."))
        return errors

    async def execute(
        self,
        *,
        user_id: str,
        cover_user_id: str | None,
        back_on: date | None,
        move_open: bool,
        by_user_id: str,
    ) -> int:
        """Returns how many open tickets were moved."""
        absence = SeAbsence(
            user_id=user_id,
            cover_user_id=cover_user_id,
            back_on=back_on,
            set_by_user_id=by_user_id,
        )
        await self._repo.upsert(absence)
        logger.info(
            "SE %s marked out by %s (cover=%s, back_on=%s)",
            user_id,
            by_user_id,
            cover_user_id,
            back_on,
        )
        moved = await self._move_open_tickets(user_id, by_user_id=by_user_id) if move_open else 0

        moved_note = f" Moved {moved} open ticket{'s' if moved != 1 else ''}." if move_open else ""
        if self._se_tickets_channel_id:
            await self._slack.send_message(
                self._se_tickets_channel_id,
                f"{describe_absence(absence)}. (set by <@{by_user_id}>){moved_note}",
            )
        if cover_user_id and cover_user_id != by_user_id:
            until = f" until {format_day(back_on)}" if back_on else ""
            await self._slack.send_dm(
                cover_user_id,
                f":palm_tree: You're covering <@{user_id}>'s new tickets{until}.{moved_note}",
            )
        return moved

    async def _move_open_tickets(self, user_id: str, *, by_user_id: str) -> int:
        """Reassign every live ticket `user_id` owns through the card's own
        SE-owner change path (card redraw + Linear assignee sync). Each ticket is
        routed individually so the round-robin balances as it goes."""
        away = await self._availability.active()
        moved = 0
        for ticket in await self._tickets.list_open_by_se_owner(user_id):
            assert ticket.id is not None
            target = await reroute_if_away(user_id, away, self._pick_owner)
            if target is None or target == user_id:
                continue
            await self._apply_se_owner_change.execute(
                SeOwnerChangePayload(ticket_id=ticket.id, owner_user_id=target),
                by_user_id=by_user_id,
            )
            moved += 1
        return moved


class MarkBack:
    """Clear an SE's absence so new tickets route to them again."""

    def __init__(
        self,
        *,
        repo: SeAvailabilityRepositoryPort,
        slack: SlackPort,
        se_tickets_channel_id: str | None,
    ) -> None:
        self._repo = repo
        self._slack = slack
        self._se_tickets_channel_id = se_tickets_channel_id

    async def execute(self, *, user_id: str, by_user_id: str) -> bool:
        """Returns whether they were marked out to begin with."""
        existed = await self._repo.delete(user_id)
        if not existed:
            return False
        logger.info("SE %s marked back by %s", user_id, by_user_id)
        if self._se_tickets_channel_id:
            by = "" if by_user_id == user_id else f" (set by <@{by_user_id}>)"
            await self._slack.send_message(
                self._se_tickets_channel_id,
                f":wave: <@{user_id}> is back — new tickets route to them again.{by}",
            )
        return True
