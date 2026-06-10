"""SubmitTicketForm — invoked on `view_submission` from either modal.

The min-spec §5 pipeline:

    1. Validate required fields                  — done in submission_payload.py
    2. Dedupe                                    — Chunk 6 (this file calls FindDedupeCandidate)
    3. (Suggested priority from matrix           — Chunk 7)
    4. INSERT ticket
    5. INSERT event_status_changes (null → New)
    6. DM the SE the §9a initial-ack draft
    7. Post the ticket card to SE_TICKETS_CHANNEL_ID

When dedupe finds a candidate the bot stashes the form payload and DMs SE the
Merge/Create-new buttons; the actual ticket isn't created until SE clicks
"Create new" (which routes back to `proceed_create_and_announce`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from customerbot.application.intake.dedupe import (
    FindDedupeCandidate,
    OfferDedupeChoice,
    StashedTicketPayload,
)
from customerbot.application.intake.submissions import (
    CSMIntakeSubmission,
    InAppBugSubmission,
    SEBugSubmission,
)
from customerbot.application.intake.ticket_card import build_blocks, fallback_text
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.tracking.comms_drafts import initial_ack
from customerbot.domain.bot_state.entities import PendingDedupeChoice
from customerbot.domain.bot_state.ports import DraftFormSessionRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import (
    Lane,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)

logger = logging.getLogger(__name__)

UNKNOWN_ORG_ID = "unknown"
"""Catch-all org for tickets whose org_id doesn't match a seeded row.

Seeded as a high-weight placeholder so an unmapped customer (e.g. a Gleap
in-app submission carrying an org_id we don't recognise yet) surfaces at high
priority and is visibly bucketed under "Unknown" on the board, rather than
sinking to the lowest tier. The SE's follow-up action is to add the real org
row and reassign. The fallback only activates when an `unknown` row exists, so
it's a no-op in environments that haven't seeded one."""


@dataclass
class SubmitResult:
    ticket: Ticket | None  # None when dedupe is pending SE confirmation
    card_message_ts: str | None = None
    pending_dedupe: PendingDedupeChoice | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SubmitTicketForm:
    def __init__(
        self,
        slack: SlackPort,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        drafts: DraftFormSessionRepositoryPort,
        find_dedupe: FindDedupeCandidate,
        offer_dedupe: OfferDedupeChoice,
        assign_priority: AssignPriority,
        se_user_id: str,
        se_tickets_channel_id: str | None,
        tech_assistance_channel_id: str | None = None,
    ) -> None:
        self._slack = slack
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._drafts = drafts
        self._find_dedupe = find_dedupe
        self._offer_dedupe = offer_dedupe
        self._assign_priority = assign_priority
        self._se_user_id = se_user_id
        self._se_tickets_channel_id = se_tickets_channel_id
        self._tech_assistance_channel_id = tech_assistance_channel_id

    async def _resolve_org(self, org_id: str) -> tuple[Org | None, str]:
        """Resolve an org_id to its row, falling back to the catch-all
        `unknown` org when the id doesn't match a seeded row.

        Returns `(org, effective_org_id)` — the effective id is threaded
        through the rest of the pipeline (dedupe, M2M link, card) so an
        unmapped ticket is both priced *and* bucketed against `unknown`.
        When no `unknown` row exists this returns `(None, org_id)`, i.e. the
        prior behaviour."""
        org = await self._orgs.get(org_id)
        if org is not None:
            return org, org_id
        fallback = await self._orgs.get(UNKNOWN_ORG_ID)
        if fallback is not None:
            logger.info(
                "org_id=%r not in table — routing ticket to catch-all org %r",
                org_id,
                UNKNOWN_ORG_ID,
            )
            return fallback, UNKNOWN_ORG_ID
        return None, org_id

    async def from_csm_intake(
        self,
        submission: CSMIntakeSubmission,
        *,
        reporter_user_id: str,
        slack_view_id: str | None = None,
        original_slack_link: str | None = None,
    ) -> SubmitResult:
        # CSM intake doesn't capture severity directly — derive from `blocking`.
        severity = Severity.BLOCKING if submission.blocking else Severity.DEGRADED
        title = _title_from_description(submission.description)
        org, org_id = await self._resolve_org(submission.org_id)
        priority = self._assign_priority.suggest(org, severity)
        ticket = Ticket(
            title=title,
            type=TicketType.CONFIG,  # default; SE reclassifies if it turns out to be a Bug or FAQ
            subtype=TicketSubtype.SETUP_INTEGRATION,
            severity=severity,
            priority=priority,
            reporter_user_id=reporter_user_id,
            source=Source.TECH_ASSISTANCE,
            description=submission.description,
            prod_link=submission.prod_link,
            blocking_impact=submission.blocking_impact,
            deadline=submission.deadline,
            original_slack_link=original_slack_link,
        )
        return await self._run_pipeline(
            ticket,
            kind="csm_intake",
            org_id=org_id,
            reporter_user_id=reporter_user_id,
            slack_view_id=slack_view_id,
            original_slack_link=original_slack_link,
        )

    async def from_se_bug(
        self,
        submission: SEBugSubmission,
        *,
        reporter_user_id: str,
        slack_view_id: str | None = None,
        original_slack_link: str | None = None,
    ) -> SubmitResult:
        org, org_id = await self._resolve_org(submission.org_id)
        priority = self._assign_priority.suggest(org, submission.severity)
        ticket = Ticket(
            title=submission.summary,
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=submission.severity,
            priority=priority,
            lane=Lane.SE_ACTION,
            reporter_user_id=reporter_user_id,
            source=submission.source,
            description=submission.description,
            affected_user=submission.affected_user,
            replay_link=submission.replay_link,
            original_slack_link=original_slack_link,
        )
        return await self._run_pipeline(
            ticket,
            kind="se_bug",
            org_id=org_id,
            reporter_user_id=reporter_user_id,
            slack_view_id=slack_view_id,
            original_slack_link=original_slack_link,
        )

    async def from_in_app_webhook(self, submission: InAppBugSubmission) -> SubmitResult:
        """Create a ticket from the §3c in-product webhook payload.

        Source is forced to `IN_APP` and the submitter's email + page URL get
        woven into the description so SE has the context without us inventing
        a separate "in-app context" column. Goes through the standard dedupe
        pipeline; if it survives, a read-only feed entry is posted to
        `#tech-assistance` per §3d.
        """
        org, org_id = await self._resolve_org(submission.org_id)
        # In-app users almost always trip "Unsure" — they didn't tick a
        # severity radio. SE bumps in the override DM if needed.
        priority = self._assign_priority.suggest(org, Severity.UNSURE)
        # Ticket owner on our side is SE — the in-app submitter has no Slack
        # identity to address, so any dedupe / override DM has to land
        # somewhere reachable. The submitter's identity is preserved via
        # `affected_user` + the in-app stanza in the description.
        title = _title_from_description(submission.description)
        described = _compose_in_app_description(submission)
        ticket = Ticket(
            title=title,
            type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            severity=Severity.UNSURE,
            priority=priority,
            lane=Lane.SE_ACTION,
            reporter_user_id=self._se_user_id,
            source=Source.IN_APP,
            description=described,
            affected_user=submission.user_email or submission.user_id,
            replay_link=submission.session_replay_url,
            screenshot_url=submission.screenshot_url,
            prod_link=submission.page_url,
        )
        result = await self._run_pipeline(
            ticket,
            kind="in_app_bug",
            org_id=org_id,
            reporter_user_id=self._se_user_id,
            slack_view_id=None,
            original_slack_link=None,
        )
        if result.ticket is not None:
            await self._post_tech_assistance_feed_entry(result.ticket, submission, org=org)
        return result

    async def _post_tech_assistance_feed_entry(
        self, ticket: Ticket, submission: InAppBugSubmission, *, org: Org | None
    ) -> None:
        if not self._tech_assistance_channel_id:
            logger.info(
                "TECH_ASSISTANCE_CHANNEL_ID not configured — skipping in-app feed entry for %s",
                ticket.display_id,
            )
            return
        org_label = org.name if org is not None else submission.org_id
        blocks = _in_app_feed_blocks(ticket, submission, org_label=org_label)
        await self._slack.send_blocks(
            self._tech_assistance_channel_id,
            blocks,
            text=f":incoming_envelope: In-app bug — {ticket.display_id}",
        )

    async def _run_pipeline(
        self,
        ticket: Ticket,
        *,
        kind: str,
        org_id: str,
        reporter_user_id: str,
        slack_view_id: str | None,
        original_slack_link: str | None,
    ) -> SubmitResult:
        # Step 2 — dedupe check against live tickets.
        candidate = await self._find_dedupe.execute(
            org_id=org_id,
            prod_link=ticket.prod_link,
            severity=ticket.severity,
            feature=ticket.feature,
            summary=ticket.title,
            description=ticket.description,
        )
        if candidate is not None:
            org = await self._orgs.get(org_id)
            payload = StashedTicketPayload(
                kind=kind,
                ticket_dump=ticket.model_dump(mode="json"),
                org_id=org_id,
                reporter_user_id=reporter_user_id,
                slack_view_id=slack_view_id,
                original_slack_link=original_slack_link,
            )
            affected = await self._tickets.list_orgs(candidate.ticket.id or 0)
            affected_names = []
            for oid in affected:
                o = await self._orgs.get(oid)
                affected_names.append(o.name if o else oid)
            pending = await self._offer_dedupe.execute(
                candidate=candidate,
                payload=payload,
                sender_user_id=reporter_user_id,
                affected_org_names=affected_names,
            )
            logger.info(
                "Dedupe match for proposed ticket — pending #%s against %s (%s, %.2f)",
                pending.id,
                candidate.ticket.display_id,
                candidate.criterion,
                candidate.score,
            )
            # Don't consume the draft session — SE might click "Create new",
            # and the draft sweeper will tidy it up after 30 min anyway.
            _ = org  # silence unused
            return SubmitResult(ticket=None, pending_dedupe=pending)
        return await self.proceed_create_and_announce(
            ticket, org_id=org_id, slack_view_id=slack_view_id
        )

    async def proceed_create_and_announce(
        self,
        ticket: Ticket,
        *,
        org_id: str,
        slack_view_id: str | None,
        deadline: date | None = None,
    ) -> SubmitResult:
        """Steps 4–7 of the §5 pipeline. Public so the dedupe `Create new`
        handler can call back in after SE decides to proceed."""
        now = _utcnow()
        ticket.created_at = now
        ticket.updated_at = now

        # 4. Create the ticket.
        created = await self._tickets.create(ticket)
        assert created.id is not None

        # M2M: link the org. Falls back gracefully if the org row is missing —
        # the SE/CSM is expected to have picked from the canonical dropdown.
        org = await self._orgs.get(org_id)
        if org is not None:
            await self._tickets.add_org(created.id, org.id)
        else:
            logger.warning(
                "Submitted ticket %s references missing org_id=%s; "
                "form dropdown should have prevented this",
                created.display_id,
                org_id,
            )

        # 5. Event log: null → New.
        await self._events.append_status_change(
            ticket_id=created.id,
            from_status=None,
            to_status=TicketStatus.NEW,
            by_user_id=created.reporter_user_id,
            at=now,
            note=f"created via {created.source.value}",
        )

        # 6. DM the SE the §9a initial-acknowledgement draft.
        await self._dm_initial_ack_draft(created, org)

        # 7. Post the ticket card. Loop in the org's CSM as a stakeholder so
        # they can follow the ticket without being the SE working it.
        org_names = [org.name] if org is not None else []
        csm_ids = [org.csm_user_id] if org is not None and org.csm_user_id else []
        card_ts = await self._post_ticket_card(created, org_names, csm_ids)
        if card_ts is not None:
            await self._tickets.update_card_message(
                created.id, self._se_tickets_channel_id or "", card_ts
            )
            # Reflect on the returned entity for caller convenience.
            created.card_channel_id = self._se_tickets_channel_id
            created.card_message_ts = card_ts

        # 7b. Priority audit + override-buttons DM (flow §7a).
        await self._assign_priority.record_and_offer_override(
            created, org, se_user_id=self._se_user_id
        )

        # 7c. Confirm back to whoever logged it. Everything else here DMs the
        # SE (ack draft, override buttons, card) — so without this a teammate
        # who isn't the SE submits the form and sees *nothing* happen, and
        # reasonably concludes it didn't work. Skip when the reporter is the
        # SE, who already gets the richer ack-draft DM.
        await self._confirm_to_submitter(created, org)

        # Drop the draft session — submission consumed it.
        if slack_view_id is not None:
            existing = await self._drafts.get_by_view_id(slack_view_id)
            if existing is not None and existing.id is not None:
                await self._drafts.delete(existing.id)

        return SubmitResult(ticket=created, card_message_ts=card_ts)

    async def proceed_create_from_pending(self, payload: StashedTicketPayload) -> SubmitResult:
        """Reconstruct a Ticket from a stashed payload and run steps 4–7.

        Invoked by the dedupe `Create new` button handler.
        """
        ticket = Ticket.model_validate(payload.ticket_dump)
        # Re-anchor created/updated timestamps to now — the stash was earlier.
        return await self.proceed_create_and_announce(
            ticket,
            org_id=payload.org_id,
            slack_view_id=payload.slack_view_id,
        )

    async def _post_ticket_card(
        self, ticket: Ticket, org_names: list[str], csm_user_ids: list[str] | None = None
    ) -> str | None:
        if not self._se_tickets_channel_id:
            logger.warning(
                "SE_TICKETS_CHANNEL_ID not configured — ticket %s created but no card posted",
                ticket.display_id,
            )
            return None
        blocks = build_blocks(ticket, org_names, csm_user_ids)
        return await self._slack.send_blocks(
            self._se_tickets_channel_id,
            blocks,
            text=fallback_text(ticket),
        )

    async def _confirm_to_submitter(self, ticket: Ticket, org: Org | None) -> None:
        """DM the reporter a short receipt that their ticket was logged.

        No-op when the reporter is the SE (who already receives the initial-ack
        draft DM) or when there's no usable reporter id (e.g. in-app webhooks,
        where the reporter is set to the SE anyway)."""
        reporter = ticket.reporter_user_id
        if not reporter or reporter == self._se_user_id:
            return
        org_label = org.name if org is not None else None
        await self._slack.send_dm_blocks(
            reporter,
            _submitter_confirmation_blocks(ticket, org_label),
            text=f"Ticket logged: {ticket.display_id}",
        )

    async def _dm_initial_ack_draft(self, ticket: Ticket, org: Org | None) -> None:
        """§9a — Chunk 11 templates own the rendering; we just DM what they return."""
        draft = initial_ack(ticket, org)
        await self._slack.send_dm_blocks(
            self._se_user_id,
            draft.blocks(),
            text=f"Initial-ack draft: {ticket.display_id}",
        )


def _submitter_confirmation_blocks(
    ticket: Ticket, org_label: str | None
) -> list[dict[str, object]]:
    """Receipt DM'd to the person who logged a ticket (when they aren't the SE)."""
    org_bit = f" for *{org_label}*" if org_label else ""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: Thanks — your ticket *{ticket.display_id}*"
                    f"{org_bit} has been logged and the support team has been notified.\n"
                    f"_{ticket.title}_ · *{ticket.priority.value}*"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "You'll be looped in here if we need more detail.",
                }
            ],
        },
    ]


def _title_from_description(description: str) -> str:
    """Derive a one-line title from a free-text description (CSM intake has no
    dedicated summary field — §4a). First line, truncated to 140 chars."""
    first_line = description.strip().splitlines()[0] if description.strip() else "(no title)"
    return first_line[:140]


def _compose_in_app_description(submission: InAppBugSubmission) -> str:
    """Bake the in-app context (page URL, user email) into the description.

    Keeps the structured fields (`prod_link`, `affected_user`, etc.) the
    canonical home but mirrors them into the description so SE sees the
    context inline without having to inspect side fields.
    """
    parts: list[str] = []
    if submission.description:
        parts.append(submission.description.strip())
    parts.append("")
    parts.append(f"_In-app submission — reported on {submission.page_url}._")
    if submission.user_email:
        parts.append(f"_Submitter: {submission.user_email}_")
    return "\n".join(parts).strip()


def _in_app_feed_blocks(
    ticket: Ticket,
    submission: InAppBugSubmission,
    *,
    org_label: str,
) -> list[dict[str, object]]:
    """§3d — read-only feed entry posted to #tech-assistance for visibility."""
    context_bits: list[str] = []
    if submission.session_replay_url:
        context_bits.append(f"<{submission.session_replay_url}|Session replay>")
    if submission.screenshot_url:
        context_bits.append(f"<{submission.screenshot_url}|Screenshot>")
    context_bits.append(f"<{submission.page_url}|Page URL>")
    blocks: list[dict[str, object]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":incoming_envelope: *In-app bug submitted* — "
                    f"{ticket.display_id} _{ticket.title}_\n"
                    f"From *{org_label}* "
                    f"({submission.user_email or submission.user_id})"
                ),
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " · ".join(context_bits)}],
        },
    ]
    return blocks
