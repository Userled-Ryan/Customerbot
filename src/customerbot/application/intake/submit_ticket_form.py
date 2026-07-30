"""SubmitTicketForm — invoked on `view_submission` from either modal.

The min-spec §5 pipeline:

    1. Validate required fields                  — done in submission_payload.py
    2. Dedupe                                    — Chunk 6 (this file calls FindDedupeCandidate)
    3. (Suggested priority from matrix           — Chunk 7)
    4. INSERT ticket
    5. INSERT event_status_changes (null → New)
    6. Post the ticket card to SE_TICKETS_CHANNEL_ID

When dedupe finds a candidate the bot stashes the form payload and DMs SE the
Merge/Create-new buttons; the actual ticket isn't created until SE clicks
"Create new" (which routes back to `proceed_create_and_announce`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
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
from customerbot.application.intake.support_threads import attach_source_thread
from customerbot.application.intake.ticket_card import (
    build_blocks,
    fallback_text,
    resolve_se_owner_options,
)
from customerbot.application.linear.sync import LinearSync
from customerbot.application.priority.assign import AssignPriority
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
    Priority,
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


class OrgCreationError(ValueError):
    """Raised when inline "create new org" input can't be turned into an org.

    `field` is a stable, integration-agnostic marker ("name" | "channel") the
    Slack handler maps to the offending modal block so the SE sees the error on
    the right field rather than a silently-closed modal.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _slugify_org_name(name: str) -> str:
    """Derive a short org slug (the primary key) from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "org"


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
        support_channel_ids: Collection[str] = (),
        se_owner_user_ids: Collection[str] = (),
        linear: LinearSync | None = None,
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
        # Round-robin pool for the default SE owner (balanced by open load). When
        # empty or a single member, falls back to always `se_user_id`.
        self._se_owner_user_ids = se_owner_user_ids
        self._se_tickets_channel_id = se_tickets_channel_id
        # #userled-support, where the in-app read-only feed entry is posted.
        self._tech_assistance_channel_id = tech_assistance_channel_id
        # Channels whose threads join the 🎫→✅ status loop (support + Gleap).
        self._support_channel_ids = support_channel_ids
        self._linear = linear

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

    async def create_org_from_intake(
        self, *, name: str, channel_id: str, owner_id: str | None
    ) -> str:
        """Create a new org inline from the intake modal and return its id.

        Validates the two required fields (name + channel) and that the channel
        isn't already mapped to another org (the `orgs.slack_channel_id` UNIQUE
        constraint would otherwise blow up on insert). The id is a slug derived
        from the name, de-duplicated against existing rows so a name clash can't
        silently overwrite an unrelated org. `owner_id` becomes the org's CSM.
        Raises `OrgCreationError(field, ...)` so the caller can route the error
        back to the right modal field.
        """
        name = name.strip()
        channel_id = channel_id.strip()
        if not name:
            raise OrgCreationError("name", "Enter a name for the new org.")
        if not channel_id:
            raise OrgCreationError("channel", "Enter the customer's Slack channel ID.")
        # Slack channel ids are C… (public/private) or G… (legacy private group).
        if not channel_id.startswith(("C", "G")):
            raise OrgCreationError(
                "channel", "That doesn't look like a channel ID — it should start with C."
            )
        existing = await self._orgs.find_by_slack_channel(channel_id)
        if existing is not None:
            raise OrgCreationError(
                "channel", f"That channel is already mapped to “{existing.name}”."
            )

        base = _slugify_org_name(name)
        org_id = base
        suffix = 2
        while await self._orgs.get(org_id) is not None:
            org_id = f"{base}-{suffix}"
            suffix += 1

        await self._orgs.upsert(
            Org(
                id=org_id,
                name=name,
                slack_channel_id=channel_id,
                csm_user_id=owner_id,
            )
        )
        logger.info(
            "Created org %r (%s) inline from intake — channel=%s owner=%s",
            org_id,
            name,
            channel_id,
            owner_id,
        )
        return org_id

    async def from_csm_intake(
        self,
        submission: CSMIntakeSubmission,
        *,
        reporter_user_id: str,
        slack_view_id: str | None = None,
        original_slack_link: str | None = None,
    ) -> SubmitResult:
        # DORMANT (2026-07-02): unreachable since the CSM intake modal was
        # retired (see OpenIntakeModal._choose_modal). Note this path prices
        # config via the customer-weight matrix rather than the P4/P2 rule in
        # `_build_config_ticket` — reconcile that if this is ever revived.
        # Kept during a trial; REMOVE this method if we don't revert.
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
        if submission.ticket_type == TicketType.CONFIG:
            ticket = self._build_se_action_ticket(
                submission,
                reporter_user_id=reporter_user_id,
                original_slack_link=original_slack_link,
                ticket_type=TicketType.CONFIG,
                subtype=TicketSubtype.SETUP_INTEGRATION,
            )
        elif submission.ticket_type == TicketType.FEATURE_REQUEST:
            # Product change: a prod improvement / enhancement, not a defect.
            # Priced and laned exactly like Config (default P4, P2 if urgent, SE
            # lane); ENHANCEMENT is the catch-all subtype — SE reclassifies to
            # new-capability from the card if it's a genuinely new feature.
            ticket = self._build_se_action_ticket(
                submission,
                reporter_user_id=reporter_user_id,
                original_slack_link=original_slack_link,
                ticket_type=TicketType.FEATURE_REQUEST,
                subtype=TicketSubtype.ENHANCEMENT,
            )
        elif submission.ticket_type == TicketType.CSM_HELP:
            # CSM Help: an extra pair of hands with normally-CSM work (deck
            # building, coverage during an absence, etc.). Only raised when a CSM
            # is stretched, so it's *always* urgent (forced below) and always
            # starts unassigned — whoever has capacity claims it from the card.
            # The specific ask lives in the description; CSM_ASSISTANCE is the
            # single catch-all subtype.
            ticket = self._build_se_action_ticket(
                submission,
                reporter_user_id=reporter_user_id,
                original_slack_link=original_slack_link,
                ticket_type=TicketType.CSM_HELP,
                subtype=TicketSubtype.CSM_ASSISTANCE,
            )
        else:
            # SE bug intake doesn't capture severity directly — derive from
            # `blocking`, mirroring the CSM intake flow. SE reclassifies from the
            # ticket card if needed.
            severity = Severity.BLOCKING if submission.blocking else Severity.DEGRADED
            priority = self._assign_priority.suggest(org, severity)
            subtype = (
                TicketSubtype.PLATFORM_WIDE
                if submission.platform_wide
                else TicketSubtype.CUSTOMER_SPECIFIC
            )
            ticket = Ticket(
                title=submission.summary[:45],
                type=TicketType.BUG,
                subtype=subtype,
                severity=severity,
                priority=priority,
                lane=Lane.SE_ACTION,
                reporter_user_id=reporter_user_id,
                source=submission.source,
                description=submission.description,
                deadline=submission.deadline,
                affected_user=submission.affected_user,
                replay_link=submission.replay_link,
                campaign_url=submission.campaign_url,
                original_slack_link=original_slack_link,
            )
        if submission.ticket_type == TicketType.CSM_HELP:
            # CSM Help is always urgent, but stays unassigned so someone can
            # claim it from the card — don't stamp an SE owner here.
            self._apply_urgent(ticket, assign_owner=False)
        elif submission.urgent:
            self._apply_urgent(ticket)
        return await self._run_pipeline(
            ticket,
            kind="se_bug",
            org_id=org_id,
            reporter_user_id=reporter_user_id,
            slack_view_id=slack_view_id,
            original_slack_link=original_slack_link,
        )

    def _apply_urgent(self, ticket: Ticket, *, assign_owner: bool = True) -> None:
        """Stamp the urgent policy onto a freshly-built ticket.

        Urgent tickets have no deadline (the whole point — they replace sub-48h
        deadlines), are forced to P1, ride the SE lane, and are assigned to the
        configured SE (currently everyone; the card dropdown reassigns later).
        The Linear Urgent-section mirror and hourly nag key off `is_urgent`
        (`urgent` + still NEW), so nothing else needs setting here.

        `assign_owner=False` skips the owner stamp so the ticket stays unassigned
        (CSM Help is always urgent but must be claimed from the card)."""
        ticket.urgent = True
        ticket.priority = Priority.P1
        ticket.deadline = None
        ticket.lane = Lane.SE_ACTION
        if assign_owner:
            ticket.se_owner_user_id = self._se_user_id

    @staticmethod
    def _build_se_action_ticket(
        submission: SEBugSubmission,
        *,
        reporter_user_id: str,
        original_slack_link: str | None,
        ticket_type: TicketType,
        subtype: TicketSubtype,
    ) -> Ticket:
        """Build a non-bug SE-action ticket (Config or Product change) from the
        SE intake form.

        These aren't defects (enable a feature-flagged integration, verify a
        domain, a prod improvement request, etc.), so they bypass the
        customer-weight priority matrix: default P4, bumped to P2 only when the
        SE flags it urgent. The caller passes the catch-all `subtype` — the SE
        refines it from the ticket card's reclassify modal if needed. Severity is
        meaningless for a non-bug action, so it stays at the `UNSURE` default.
        """
        priority = Priority.P2 if submission.blocking else Priority.P4
        return Ticket(
            title=submission.summary[:45],
            type=ticket_type,
            subtype=subtype,
            priority=priority,
            lane=Lane.SE_ACTION,
            reporter_user_id=reporter_user_id,
            source=submission.source,
            description=submission.description,
            deadline=submission.deadline,
            affected_user=submission.affected_user,
            replay_link=submission.replay_link,
            campaign_url=submission.campaign_url,
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
        # severity radio. SE bumps from the ticket card if needed.
        priority = self._assign_priority.suggest(org, Severity.UNSURE)
        # Ticket owner on our side is SE — the in-app submitter has no Slack
        # identity to address, so any dedupe DM has to land somewhere
        # reachable. The submitter's identity is preserved via
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

    async def _pick_se_owner(self) -> str:
        """Balanced round-robin: the pool member with the fewest active tickets,
        tie-broken deterministically by pool order. Load is measured live from
        Linear (issues assigned to the SE in the customerbot projects, excluding
        Done / In Review), which reflects real current workload; it falls back
        to the local open-ticket count when Linear can't answer (off/unreachable,
        or an SE isn't mapped). Falls back to the single configured SE when the
        pool is empty or has one member."""
        pool = list(self._se_owner_user_ids)
        if len(pool) <= 1:
            return self._se_user_id
        counts: dict[str, int] | None = None
        if self._linear is not None:
            counts = await self._linear.active_se_load(pool)
        if counts is None:
            counts = await self._tickets.count_open_by_se_owner()
        return min(pool, key=lambda uid: (counts.get(uid, 0), pool.index(uid)))

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
        # SE owner defaults via balanced round-robin over the SE pool — not
        # exposed to the logger, reassigned later from the card dropdown. Set
        # here (the one create funnel) so every intake path + dedupe "Create
        # new" gets it. CSM Help is the exception: it deliberately stays
        # unassigned until someone claims it from the card.
        if ticket.se_owner_user_id is None and ticket.type != TicketType.CSM_HELP:
            ticket.se_owner_user_id = await self._pick_se_owner()

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

        # 7a2. If this ticket was raised from a thread we track — an internal
        # support channel (#userled-support / Gleap) or a customer channel —
        # attach it and mark it in flight (🎫) so the channel shows at a glance
        # that it's being worked. A customer thread also gets a short in-thread
        # acknowledgement naming the ticket. Unmapped / no-thread: untouched.
        await attach_source_thread(
            self._tickets,
            self._slack,
            self._orgs,
            ticket_id=created.id,
            display_id=created.display_id,
            link=created.original_slack_link,
            support_channel_ids=self._support_channel_ids,
            by_user_id=created.reporter_user_id,
            now=now,
        )

        # 7b. Priority audit row (flow §7a). Overrides happen on the ticket
        # card's priority dropdown, so no separate override DM is sent.
        await self._assign_priority.record_assignment(created)

        # 7c. Confirm back to whoever logged it. The ticket card lands in the
        # feed channel, not a DM — so without this a teammate who isn't the SE
        # submits the form and sees *nothing* happen, and reasonably concludes
        # it didn't work. Skip when the reporter is the SE.
        await self._confirm_to_submitter(created, org)

        # Drop the draft session — submission consumed it.
        if slack_view_id is not None:
            existing = await self._drafts.get_by_view_id(slack_view_id)
            if existing is not None and existing.id is not None:
                await self._drafts.delete(existing.id)

        # 8. Mirror into Linear (every ticket — dev handover + CTO reporting).
        # Best-effort and last, so a Linear hiccup can't affect the Slack flow.
        if self._linear is not None:
            await self._linear.mirror_new_ticket(created)

        # 9. Urgent tickets alert the SE owner immediately (don't wait up to an
        # hour for the first nag). The hourly UrgentNag job takes over from here.
        if created.is_urgent:
            await self._notify_urgent_owner(created)

        return SubmitResult(ticket=created, card_message_ts=card_ts)

    async def _notify_urgent_owner(self, ticket: Ticket) -> None:
        """DM the SE owner that an urgent ticket was just logged. Best-effort:
        a DM failure must not derail the create pipeline."""
        owner = ticket.se_owner_user_id or self._se_user_id
        if not owner:
            return
        link = ticket.linear_issue_url or ticket.original_slack_link
        link_bit = f"\n<{link}|Open the ticket>" if link else ""
        text = f":rotating_light: Urgent ticket logged: {ticket.display_id}"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":rotating_light: *Urgent ticket* — *{ticket.display_id}*\n"
                        f"_{ticket.title}_ · *{ticket.priority.value}*{link_bit}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "You'll be reminded hourly until you move it to "
                            "In progress or Resolved."
                        ),
                    }
                ],
            },
        ]
        try:
            await self._slack.send_dm_blocks(owner, blocks, text=text)
        except Exception:
            logger.exception("Urgent-owner DM failed for ticket %s", ticket.id)

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
        se_owner_options = await resolve_se_owner_options(self._slack, ticket.se_owner_user_id)
        blocks = build_blocks(ticket, org_names, csm_user_ids, se_owner_options)
        return await self._slack.send_blocks(
            self._se_tickets_channel_id,
            blocks,
            text=fallback_text(ticket),
        )

    async def _confirm_to_submitter(self, ticket: Ticket, org: Org | None) -> None:
        """DM the reporter a short receipt that their ticket was logged.

        No-op when the reporter is the SE (who sees the ticket card in the feed
        channel) or when there's no usable reporter id (e.g. in-app webhooks,
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
    dedicated summary field — §4a). First line, truncated to 45 chars."""
    first_line = description.strip().splitlines()[0] if description.strip() else "(no title)"
    return first_line[:45]


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
        context_bits.append(f"<{submission.session_replay_url}|Link>")
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
