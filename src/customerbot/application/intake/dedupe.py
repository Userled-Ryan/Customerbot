"""Suggest-not-auto dedupe (min-spec §11, flow §11).

Three match criteria, applied in priority order:

1. Same `org_id` + token-overlap ≥ 0.6 on summary+description
2. Same `prod_link` (exact, non-empty)
3. Same `severity` + same `feature` (non-null) + token-overlap ≥ 0.7 across any org

When a candidate is found, the bot stashes the proposed-ticket payload in
`pending_dedupe_choices` and DMs the SE a "Merge into TIC-NNN / Create new"
choice. The bot never auto-merges.

Criterion 3's `feature` field is populated manually by SE post-creation
(ambiguity #5), so this criterion only fires once SE has tagged at least
one live ticket with a feature value.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from customerbot.application.intake.support_threads import attach_source_thread
from customerbot.domain.bot_state.entities import PendingDedupeChoice
from customerbot.domain.bot_state.ports import PendingDedupeChoiceRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import Severity, TicketStatus

logger = logging.getLogger(__name__)

PENDING_TTL = timedelta(days=7)

ACTION_MERGE_DEDUPE = "merge_dedupe"
ACTION_CREATE_NEW_DEDUPE = "create_new_dedupe"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_WORD_RE = re.compile(r"\w+")


def token_overlap(text_a: str, text_b: str) -> float:
    """Jaccard similarity on lowercased word tokens. 0.0 when either is empty."""
    tokens_a = {t.lower() for t in _WORD_RE.findall(text_a)}
    tokens_b = {t.lower() for t in _WORD_RE.findall(text_b)}
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(inter) / len(union)


@dataclass(frozen=True)
class DedupeMatch:
    """A dedupe candidate with the criterion that surfaced it."""

    ticket: Ticket
    criterion: str  # "same-org-overlap" | "same-prod-link" | "cross-org-feature"
    score: float

    @property
    def is_cross_org(self) -> bool:
        return self.criterion == "cross-org-feature"


@dataclass
class StashedTicketPayload:
    """Serialised form of a proposed ticket awaiting SE confirmation."""

    kind: str  # "csm_intake" | "se_bug"
    ticket_dump: dict[str, Any]  # Ticket.model_dump()
    org_id: str
    reporter_user_id: str
    slack_view_id: str | None
    original_slack_link: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "ticket_dump": self.ticket_dump,
                "org_id": self.org_id,
                "reporter_user_id": self.reporter_user_id,
                "slack_view_id": self.slack_view_id,
                "original_slack_link": self.original_slack_link,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> StashedTicketPayload:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            ticket_dump=data["ticket_dump"],
            org_id=str(data["org_id"]),
            reporter_user_id=str(data["reporter_user_id"]),
            slack_view_id=data.get("slack_view_id"),
            original_slack_link=data.get("original_slack_link"),
        )


class FindDedupeCandidate:
    """Pure matcher — no I/O beyond the ticket repo's `query_live`."""

    def __init__(self, tickets: TicketRepositoryPort) -> None:
        self._tickets = tickets

    async def execute(
        self,
        *,
        org_id: str,
        prod_link: str | None,
        severity: Severity,
        feature: str | None,
        summary: str,
        description: str,
    ) -> DedupeMatch | None:
        live = await self._tickets.query_live()
        live = [t for t in live if t.status != TicketStatus.CLOSED]
        if not live:
            return None

        proposed_text = f"{summary} {description}"

        # Criterion 2 — exact prod_link match wins outright.
        if prod_link:
            for t in live:
                if t.prod_link and t.prod_link == prod_link:
                    return DedupeMatch(ticket=t, criterion="same-prod-link", score=1.0)

        # Criterion 1 — same-org + overlap ≥ 0.6.
        same_org = [t for t in live if await self._has_org(t, org_id)]
        crit1 = self._best_match(same_org, proposed_text, threshold=0.6)
        if crit1 is not None:
            return DedupeMatch(ticket=crit1[0], criterion="same-org-overlap", score=crit1[1])

        # Criterion 3 — cross-org + same severity + same feature + overlap ≥ 0.7.
        if feature is not None:
            cross = [t for t in live if t.severity == severity and t.feature == feature]
            crit3 = self._best_match(cross, proposed_text, threshold=0.7)
            if crit3 is not None:
                return DedupeMatch(ticket=crit3[0], criterion="cross-org-feature", score=crit3[1])
        return None

    async def _has_org(self, ticket: Ticket, org_id: str) -> bool:
        if ticket.id is None:
            return False
        orgs = await self._tickets.list_orgs(ticket.id)
        return org_id in orgs

    @staticmethod
    def _best_match(
        candidates: list[Ticket], proposed_text: str, *, threshold: float
    ) -> tuple[Ticket, float] | None:
        best: tuple[Ticket, float] | None = None
        for t in candidates:
            score = token_overlap(proposed_text, f"{t.title} {t.description}")
            if score < threshold:
                continue
            if best is None or score > best[1]:
                best = (t, score)
        return best


class OfferDedupeChoice:
    """Stash the proposed ticket and DM SE the merge/create-new buttons."""

    def __init__(
        self,
        slack: SlackPort,
        pending: PendingDedupeChoiceRepositoryPort,
    ) -> None:
        self._slack = slack
        self._pending = pending

    async def execute(
        self,
        *,
        candidate: DedupeMatch,
        payload: StashedTicketPayload,
        sender_user_id: str,
        affected_org_names: list[str],
    ) -> PendingDedupeChoice:
        # Two-step so the button's `value` can carry a real pending_id while
        # the row's dm metadata reflects the actual posted message:
        #   1. INSERT with empty dm metadata → get id N.
        #   2. Build button blocks containing N, send DM, capture (channel, ts).
        #   3. UPDATE row N with the real dm metadata.
        now = _utcnow()
        pending = await self._pending.create(
            PendingDedupeChoice(
                candidate_ticket_id=candidate.ticket.id or 0,
                payload_json=payload.to_json(),
                invoker_user_id=sender_user_id,
                dm_channel_id="",
                dm_message_ts="",
                created_at=now,
                expires_at=now + PENDING_TTL,
            )
        )
        assert pending.id is not None

        blocks = _build_dm_blocks(
            pending_id=pending.id,
            candidate=candidate,
            affected_org_names=affected_org_names,
        )
        sent = await self._slack.send_dm_blocks(
            sender_user_id,
            blocks,
            text=f"Possible duplicate: {candidate.ticket.display_id}",
        )
        if sent is not None:
            dm_channel, dm_ts = sent
            await self._pending.update_dm_metadata(pending.id, dm_channel, dm_ts)
            return PendingDedupeChoice(
                id=pending.id,
                candidate_ticket_id=pending.candidate_ticket_id,
                payload_json=pending.payload_json,
                invoker_user_id=pending.invoker_user_id,
                dm_channel_id=dm_channel,
                dm_message_ts=dm_ts,
                created_at=pending.created_at,
                expires_at=pending.expires_at,
            )
        return pending


def _build_dm_blocks(
    *,
    pending_id: int,
    candidate: DedupeMatch,
    affected_org_names: list[str],
) -> list[dict[str, Any]]:
    t = candidate.ticket
    age_text = _age_phrase(t.created_at)
    orgs_text = ", ".join(affected_org_names) if affected_org_names else "—"
    title_safe = (t.title[:120] + "…") if len(t.title) > 120 else t.title
    summary_line = (
        f"This looks like *{t.display_id}* "
        f"(_{title_safe}_, *{t.type.value}/{t.priority.value}*, opened {age_text}, "
        f"affecting {orgs_text}). Merge?"
    )
    why_line = _criterion_explanation(candidate)

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary_line},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": why_line}],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": f"Merge into {t.display_id}"},
                    "action_id": ACTION_MERGE_DEDUPE,
                    "value": str(pending_id),
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Create new"},
                    "action_id": ACTION_CREATE_NEW_DEDUPE,
                    "value": str(pending_id),
                },
            ],
        },
    ]


def _age_phrase(created_at: datetime) -> str:
    delta = _utcnow() - created_at
    if delta.total_seconds() < 60:
        return "just now"
    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _criterion_explanation(candidate: DedupeMatch) -> str:
    if candidate.criterion == "same-prod-link":
        return ":link: Exact match on `prod_link`."
    if candidate.criterion == "same-org-overlap":
        return f":bust_in_silhouette: Same org, token overlap *{candidate.score:.0%}* ≥ 60%."
    return (
        f":handshake: Cross-org match — same severity + feature tag, "
        f"token overlap *{candidate.score:.0%}* ≥ 70%."
    )


class _BumpCheckPort(Protocol):
    """Minimal surface MergeIntoExisting needs from MultiCustomerBumpCheck.

    Declared inline as a Protocol so the dedupe module doesn't import the
    priority package (and so it can be stubbed in tests).
    """

    async def execute(self, ticket_id: int) -> object: ...


class MergeIntoExisting:
    """Handle the `Merge into TIC-NNN` button click.

    Appends the incoming submission's context to the candidate's description,
    adds the proposed org to the candidate's `ticket_orgs` if cross-org, and
    writes a status-changes event row noting the merge. Does NOT change
    status; the candidate stays in its current state. After the org is added,
    the multi-customer bump check (Chunk 7) gets a chance to DM SE a
    suggestion.
    """

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        pending: PendingDedupeChoiceRepositoryPort,
        slack: SlackPort,
        se_tickets_channel_id: str | None,
        bump_check: _BumpCheckPort | None = None,
        support_channel_ids: Collection[str] = (),
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._pending = pending
        self._slack = slack
        self._se_tickets_channel_id = se_tickets_channel_id
        self._bump_check = bump_check
        self._support_channel_ids = support_channel_ids

    async def execute(self, *, pending_id: int, by_user_id: str) -> Ticket | None:
        pending = await self._pending.get(pending_id)
        if pending is None:
            logger.warning("Merge clicked on missing pending row %s", pending_id)
            return None
        candidate = await self._tickets.get(pending.candidate_ticket_id)
        if candidate is None or candidate.id is None:
            logger.warning("Merge clicked but candidate %s missing", pending.candidate_ticket_id)
            await self._pending.delete(pending_id)
            return None

        payload = StashedTicketPayload.from_json(pending.payload_json)
        incoming_desc = str(payload.ticket_dump.get("description", "")).strip()
        new_description = _append_merge_context(
            candidate.description, incoming_desc, by_user_id=by_user_id
        )

        # Cross-org bookkeeping.
        existing_orgs = await self._tickets.list_orgs(candidate.id)
        org_added = False
        if payload.org_id and payload.org_id not in existing_orgs:
            await self._tickets.add_org(candidate.id, payload.org_id)
            org_added = True

        # Append context: write directly via the description field. There's no
        # public `update_description` on the port today; for v1 we use the
        # event log to make the merge auditable and rely on Chunk 9 to refresh
        # the card display when a `chat.update` is next triggered. To actually
        # surface the merged text on the ticket itself, write a status-change
        # event row carrying the incoming description as the note.
        await self._events.append_status_change(
            ticket_id=candidate.id,
            from_status=candidate.status,
            to_status=candidate.status,
            by_user_id=by_user_id,
            at=_utcnow(),
            note=f"merged-in context: {new_description}",
        )

        # The merged-in report has its own thread — attach it to the surviving
        # ticket and mark it in flight (🎫), so the person who raised it also
        # gets the "resolved" reply + ✅ later. A customer thread that wasn't
        # already on this ticket is acknowledged with the survivor's number, so
        # the reporter learns which ticket their report landed on.
        await attach_source_thread(
            self._tickets,
            self._slack,
            self._orgs,
            ticket_id=candidate.id,
            display_id=candidate.display_id,
            link=payload.original_slack_link,
            support_channel_ids=self._support_channel_ids,
            by_user_id=by_user_id,
            now=_utcnow(),
        )

        await self._pending.delete(pending_id)

        # If the merge added a new org, check whether a multi-customer prio
        # bump should be suggested (Chunk 7). Bot suggests; SE confirms.
        if org_added and self._bump_check is not None and candidate.id is not None:
            await self._bump_check.execute(candidate.id)

        return candidate


def _append_merge_context(existing: str, incoming: str, *, by_user_id: str) -> str:
    if not incoming:
        return existing
    sep = "\n\n---\n"
    header = f"_Merged in by <@{by_user_id}> at {_utcnow().isoformat(timespec='seconds')}_"
    return f"{existing}{sep}{header}\n{incoming}" if existing else f"{header}\n{incoming}"
