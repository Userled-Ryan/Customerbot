"""Customer-channel `log` / `check` detector (min-spec §3a).

Listens on every incoming message. When an internal-workspace member writes
`log` or `check` as a word-bounded token (and not negated by `no log` /
`no check`), and the thread isn't already linked to a live ticket, the bot
DMs the author an interactive button — clicking it opens the SE bug modal
pre-filled with thread context.

The actual modal opening happens at button-click time (it needs a fresh
`trigger_id` from a user interaction; message events don't carry one).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from customerbot.domain.bot_state.entities import ChannelOrgEntry
from customerbot.domain.bot_state.ports import ChannelOrgCacheRepositoryPort
from customerbot.domain.messaging.ports import SlackPort, ThreadMessage
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import LIVE_STATUSES

logger = logging.getLogger(__name__)

_TRIGGER_RE = re.compile(r"\b(log|check)\b", re.IGNORECASE)
_NEGATION_RE = re.compile(r"\bno\s+(log|check)\b", re.IGNORECASE)
_APP_MENTION_RE = re.compile(r"\blog\s+this\b", re.IGNORECASE)

OPEN_SE_BUG_FROM_DETECTOR = "open_se_bug_from_detector"
"""Slack `block_actions` ID for the DM's `Open ticket form` button."""

_BUTTON_VALUE_MAX = 1900  # Slack limit is 2000; leave headroom for JSON keys.


@dataclass(frozen=True)
class DetectorPayload:
    """Decoded button-value payload sent back when SE clicks `Open ticket form`."""

    channel_id: str
    thread_ts: str
    permalink: str
    description: str
    org_id: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def match_trigger_word(text: str) -> str | None:
    """Return the matched word ('log' or 'check') if the message triggers, else None."""
    if _NEGATION_RE.search(text):
        return None
    m = _TRIGGER_RE.search(text)
    if m is None:
        return None
    return m.group(1).lower()


def app_mention_triggers(text: str) -> bool:
    """True if an `@CustomerBot` mention text contains 'log this' (case-insensitive)."""
    return bool(_APP_MENTION_RE.search(text))


def encode_payload(payload: DetectorPayload) -> str:
    raw = json.dumps(
        {
            "channel_id": payload.channel_id,
            "thread_ts": payload.thread_ts,
            "permalink": payload.permalink,
            "description": payload.description,
            "org_id": payload.org_id,
        }
    )
    if len(raw) > _BUTTON_VALUE_MAX:
        # Truncate the description until the encoded payload fits.
        keep = _BUTTON_VALUE_MAX - (len(raw) - len(payload.description))
        truncated = payload.description[: max(keep, 0)]
        raw = json.dumps(
            {
                "channel_id": payload.channel_id,
                "thread_ts": payload.thread_ts,
                "permalink": payload.permalink,
                "description": truncated,
                "org_id": payload.org_id,
            }
        )
    return raw


def decode_payload(value: str) -> DetectorPayload:
    data = json.loads(value)
    return DetectorPayload(
        channel_id=str(data["channel_id"]),
        thread_ts=str(data["thread_ts"]),
        permalink=str(data["permalink"]),
        description=str(data.get("description", "")),
        org_id=data.get("org_id") if data.get("org_id") else None,
    )


class DetectLogCheck:
    def __init__(
        self,
        slack: SlackPort,
        orgs: OrgRepositoryPort,
        channel_org_cache: ChannelOrgCacheRepositoryPort,
        tickets: TicketRepositoryPort,
        bot_user_id: str | None,
        internal_user_group_id: str | None,
    ) -> None:
        self._slack = slack
        self._orgs = orgs
        self._channel_org_cache = channel_org_cache
        self._tickets = tickets
        self._bot_user_id = bot_user_id
        # Accept a comma-separated list of group IDs; the detector fires if the
        # sender belongs to any of them (e.g. "anyone internal" = CS + Sales + Devs).
        self._internal_user_group_ids = [
            g.strip() for g in (internal_user_group_id or "").split(",") if g.strip()
        ]

    async def execute(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        sender_user_id: str,
        text: str,
    ) -> bool:
        """Return True if the detector fired (DM sent); False otherwise."""
        if not channel_id or not thread_ts or not sender_user_id:
            return False
        if self._bot_user_id is not None and sender_user_id == self._bot_user_id:
            return False
        word = match_trigger_word(text)
        if word is None:
            return False
        if not self._internal_user_group_ids:
            logger.warning(
                "INTERNAL_USER_GROUP_ID unset — log/check detector inactive. "
                "Configure it to enable customer-channel intake."
            )
            return False
        in_any_group = False
        for group_id in self._internal_user_group_ids:
            if await self._slack.is_user_in_group(sender_user_id, group_id):
                in_any_group = True
                break
        if not in_any_group:
            return False

        permalink = self._slack.build_thread_link(channel_id, thread_ts)
        existing = await self._tickets.find_by_slack_link(permalink)
        if existing is not None and existing.status in LIVE_STATUSES:
            logger.info(
                "Suppressing log/check trigger for thread already linked to %s",
                existing.display_id,
            )
            return False

        org_id = await self._resolve_org(channel_id)
        thread_msgs = await self._slack.get_thread_messages(channel_id, thread_ts, limit=5)
        description = _draft_description(thread_msgs)
        payload = DetectorPayload(
            channel_id=channel_id,
            thread_ts=thread_ts,
            permalink=permalink,
            description=description,
            org_id=org_id,
        )
        await self._dm_open_form_button(sender_user_id, channel_id, word, payload)
        return True

    async def _resolve_org(self, channel_id: str) -> str | None:
        """Resolve channel → org via cache, populating on miss (positive or negative)."""
        cached = await self._channel_org_cache.get(channel_id)
        if cached is not None:
            return cached.org_id
        org = await self._orgs.find_by_slack_channel(channel_id)
        org_id = org.id if org is not None else None
        await self._channel_org_cache.upsert(
            ChannelOrgEntry(
                slack_channel_id=channel_id,
                org_id=org_id,
                last_synced_at=_utcnow(),
            )
        )
        return org_id

    async def _dm_open_form_button(
        self,
        user_id: str,
        channel_id: str,
        match_word: str,
        payload: DetectorPayload,
    ) -> None:
        value = encode_payload(payload)
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":mag: Detected `{match_word}` in <#{channel_id}> — "
                        f"open a ticket for this thread?"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open ticket form"},
                        "action_id": OPEN_SE_BUG_FROM_DETECTOR,
                        "value": value,
                        "style": "primary",
                    }
                ],
            },
        ]
        await self._slack.send_dm_blocks(
            user_id, blocks, text=f"Detected '{match_word}' in #{channel_id}"
        )


def _draft_description(messages: list[ThreadMessage]) -> str:
    """Join the last N thread messages into a multi-line description draft.

    Truncated to ~2000 chars total — modal `private_metadata` and prefill limits
    are both ~3000, but the button-value encoder will truncate further if needed.
    """
    if not messages:
        return ""
    lines = []
    for m in messages:
        snippet = m.text.strip()
        if not snippet:
            continue
        if m.user_id:
            lines.append(f"<@{m.user_id}>: {snippet}")
        else:
            lines.append(snippet)
    return "\n".join(lines)[:2000]
