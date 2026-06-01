"""Monthly prio-matrix-weightings-review reminder (decision #4).

On the 1st of each month at 09:00 SE-local-time the bot DMs SE with a
`[Acknowledged]` / `[Snooze 7d]` button. Tracks last ack/snooze in the
`prio_matrix_review_state` singleton table so we don't double-fire.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from customerbot.domain.bot_state.entities import PrioMatrixReviewState
from customerbot.domain.bot_state.ports import PrioMatrixReviewStateRepositoryPort
from customerbot.domain.messaging.ports import SlackPort

logger = logging.getLogger(__name__)

ACTION_ACK_MATRIX_REVIEW = "ack_matrix_review"
ACTION_SNOOZE_MATRIX_REVIEW = "snooze_matrix_review"

FIRE_HOUR = 9  # 09:00 SE-local


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown SE timezone %r — falling back to UTC for monthly review", timezone_name
        )
        return ZoneInfo("UTC")


class MonthlyMatrixReview:
    def __init__(
        self,
        slack: SlackPort,
        state: PrioMatrixReviewStateRepositoryPort,
        se_user_id: str,
        se_timezone: str,
        prio_matrix_path: str | None,
    ) -> None:
        self._slack = slack
        self._state = state
        self._se_user_id = se_user_id
        self._tz_name = se_timezone
        self._prio_matrix_path = prio_matrix_path

    async def execute(self, *, now_utc: datetime | None = None) -> bool:
        """Return True if a reminder DM was sent this tick."""
        tz = _tz(self._tz_name)
        now_naive = now_utc or _utcnow()
        local = now_naive.replace(tzinfo=UTC).astimezone(tz)
        if local.day != 1 or local.hour < FIRE_HOUR or local.hour >= FIRE_HOUR + 1:
            return False

        state = await self._state.get()
        # Honor snooze.
        if state.last_snooze_until is not None and now_naive < state.last_snooze_until:
            return False
        # Already fired this month?
        if state.last_ack_at is not None and _same_year_month(state.last_ack_at, now_naive):
            return False

        await self._slack.send_dm_blocks(
            self._se_user_id, _review_blocks(self._prio_matrix_path), text="Prio matrix review"
        )
        # Mark "fired this month" by setting last_ack_at to now until SE ack's.
        # When SE clicks Acknowledged, that handler re-stamps; until then, this
        # stops us from re-firing if the loop ticks twice in the same window.
        await self._state.update(
            PrioMatrixReviewState(
                last_ack_at=now_naive,
                last_snooze_until=state.last_snooze_until,
            ),
            now=now_naive,
        )
        return True

    async def run_loop(self, interval_seconds: int = 300) -> None:
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("Monthly matrix review loop error")
            await asyncio.sleep(interval_seconds)


def _same_year_month(a: datetime, b: datetime) -> bool:
    return a.year == b.year and a.month == b.month


def _review_blocks(matrix_path: str | None) -> list[dict[str, Any]]:
    path_text = matrix_path or "config/prio_matrix.yaml"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":calendar: *Prio matrix monthly review*\n"
                    f"Time to revisit the weightings in `{path_text}`. "
                    f"ACV × sentiment × renewal multipliers drift as customer "
                    f"context changes — confirm or adjust before priorities "
                    f"start mis-firing."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Acknowledged"},
                    "action_id": ACTION_ACK_MATRIX_REVIEW,
                    "value": "ack",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Snooze 7d"},
                    "action_id": ACTION_SNOOZE_MATRIX_REVIEW,
                    "value": "snooze7",
                },
            ],
        },
    ]


class ApplyMatrixReviewAck:
    def __init__(self, state: PrioMatrixReviewStateRepositoryPort) -> None:
        self._state = state

    async def acknowledge(self) -> None:
        now = _utcnow()
        await self._state.update(
            PrioMatrixReviewState(last_ack_at=now, last_snooze_until=None),
            now=now,
        )

    async def snooze_7d(self) -> None:
        now = _utcnow()
        await self._state.update(
            PrioMatrixReviewState(last_ack_at=None, last_snooze_until=now + timedelta(days=7)),
            now=now,
        )
