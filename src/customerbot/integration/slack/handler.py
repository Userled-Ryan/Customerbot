from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient
from starlette.responses import Response

from customerbot.application.intake.apply_se_owner import ApplySeOwnerChange
from customerbot.application.intake.dedupe import (
    ACTION_CREATE_NEW_DEDUPE,
    ACTION_MERGE_DEDUPE,
    MergeIntoExisting,
    StashedTicketPayload,
)
from customerbot.application.intake.detect_log_check import (
    OPEN_SE_BUG_FROM_DETECTOR,
    DetectLogCheck,
    app_mention_triggers,
    decode_payload,
)
from customerbot.application.intake.link_thread import OpenLinkModal, SubmitLinkThread
from customerbot.application.intake.open_intake_modal import OpenIntakeModal
from customerbot.application.intake.se_owner_actions import (
    ACTION_SET_SE_OWNER,
    SeOwnerChangePayload,
)
from customerbot.application.intake.submit_ticket_form import (
    OrgCreationError,
    SubmitTicketForm,
)
from customerbot.application.intake.ticket_card import (
    ACTION_ADD_AFFECTED_ORG,
    ACTION_DROP,
    ACTION_MOVE_TO_DEV,
    ACTION_NEEDS_ARTICLE,
    ACTION_RECLASSIFY,
    ACTION_REOPEN,
    ACTION_RESOLVED,
    ACTION_RETURN_TO_SE,
    ACTION_SET_DEADLINE,
    ACTION_SET_STAKEHOLDER,
    ACTION_TOGGLE_PLATFORM_WIDE,
    ACTION_TOGGLE_REPLY_NEEDED,
)
from customerbot.application.priority.actions import (
    ACTION_DISMISS_PRIO_DM,
    ACTION_SET_PRIORITY_PATTERN,
    PriorityChangePayload,
)
from customerbot.application.priority.monthly_review import (
    ACTION_ACK_MATRIX_REVIEW,
    ACTION_SNOOZE_MATRIX_REVIEW,
    ApplyMatrixReviewAck,
)
from customerbot.application.priority.override import ApplyPriorityChange
from customerbot.application.tracking.add_affected_org import (
    OpenAddOrgModal,
    SubmitAddAffectedOrg,
)
from customerbot.application.tracking.articles import (
    CreateArticleFromFAQ,
    RenderArticlesBoard,
)
from customerbot.application.tracking.drop import DropTicket
from customerbot.application.tracking.lane_handoff import MoveToDevAction, ReturnToSEAction
from customerbot.application.tracking.mark_in_progress_on_reply import MarkInProgressOnReply
from customerbot.application.tracking.platform_wide import TogglePlatformWide
from customerbot.application.tracking.reclassify import (
    OpenReclassifyModal,
    SubmitReclassify,
)
from customerbot.application.tracking.render_board import RenderTicketsBoard
from customerbot.application.tracking.render_report import RenderReport
from customerbot.application.tracking.reopen import ReopenTicket
from customerbot.application.tracking.reply_needed import ToggleReplyNeeded
from customerbot.application.tracking.resolve import OpenResolveModal, ResolveTicket
from customerbot.application.tracking.set_deadline import OpenSetDeadlineModal, SubmitDeadline
from customerbot.application.tracking.set_stakeholder import (
    OpenSetStakeholderModal,
    SubmitSetStakeholder,
)
from customerbot.config import SlackConfig
from customerbot.domain.bot_state.ports import PendingDedupeChoiceRepositoryPort
from customerbot.integration.slack.gateway import INTEGRATION_ID, SlackGateway
from customerbot.integration.slack.modals import (
    add_affected_org,
    csm_intake,
    link_ticket,
    reclassify,
    report_range,
    resolve,
    se_bug,
    set_deadline,
    set_stakeholder,
)
from customerbot.integration.slack.modals.submission_payload import (
    DeadlineTooSoonError,
    parse_add_affected_org,
    parse_csm_intake,
    parse_link_thread,
    parse_reclassify,
    parse_report_range,
    parse_resolve,
    parse_se_bug,
    parse_set_deadline,
    parse_set_stakeholder,
)

logger = logging.getLogger(__name__)


def _shortcut_prefill(message_text: str) -> str:
    """Prefill body for the `Log ticket` message shortcut.

    Leaves a blank first line for the SE to add their own context, then a
    `----` divider, then the quoted customer message below it. Empty when the
    message carried no text (e.g. a file-only post), so the form opens blank.
    """
    text = message_text.strip()
    if not text:
        return ""
    return f"\n----\n\n{text}"


def _default_report_range(tz_name: str) -> tuple[date, date]:
    """Default `/report` window: Monday of the current week → today, SE-local.

    Falls back to UTC if the configured timezone name is invalid.
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    monday = today - timedelta(days=today.weekday())
    return monday, today


def _action_value_as_int(body: dict[str, object]) -> int | None:
    actions = body.get("actions") or []
    if not actions:
        return None
    raw = actions[0].get("value")  # type: ignore[union-attr,index]
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


class SlackIntegration:
    """Slack integration: monitors channels, tracks conversations, and responds to commands."""

    def __init__(
        self,
        config: SlackConfig,
        ryan_user_id: str,
        open_intake_modal: OpenIntakeModal,
        submit_ticket_form: SubmitTicketForm,
        detect_log_check: DetectLogCheck,
        open_link_modal: OpenLinkModal,
        submit_link_thread: SubmitLinkThread,
        merge_into_existing: MergeIntoExisting,
        pending_dedupe_repo: PendingDedupeChoiceRepositoryPort,
        apply_priority_change: ApplyPriorityChange,
        apply_se_owner_change: ApplySeOwnerChange,
        apply_matrix_review_ack: ApplyMatrixReviewAck,
        move_to_dev_action: MoveToDevAction,
        return_to_se_action: ReturnToSEAction,
        mark_in_progress_on_reply: MarkInProgressOnReply,
        open_resolve_modal: OpenResolveModal,
        resolve_ticket: ResolveTicket,
        reopen_ticket: ReopenTicket,
        drop_ticket: DropTicket,
        open_add_org_modal: OpenAddOrgModal,
        submit_add_affected_org: SubmitAddAffectedOrg,
        open_reclassify_modal: OpenReclassifyModal,
        submit_reclassify: SubmitReclassify,
        create_article_from_faq: CreateArticleFromFAQ,
        render_articles_board: RenderArticlesBoard,
        open_set_deadline_modal: OpenSetDeadlineModal,
        submit_deadline: SubmitDeadline,
        open_set_stakeholder_modal: OpenSetStakeholderModal,
        submit_set_stakeholder: SubmitSetStakeholder,
        toggle_reply_needed: ToggleReplyNeeded,
        toggle_platform_wide: TogglePlatformWide,
        render_tickets_board: RenderTicketsBoard,
        render_report: RenderReport,
        se_timezone: str = "UTC",
    ) -> None:
        self._config = config
        self._ryan_user_id = ryan_user_id
        self._open_intake_modal = open_intake_modal
        self._submit_ticket_form = submit_ticket_form
        self._detect_log_check = detect_log_check
        self._open_link_modal = open_link_modal
        self._submit_link_thread = submit_link_thread
        self._merge_into_existing = merge_into_existing
        self._pending_dedupe_repo = pending_dedupe_repo
        self._apply_priority_change = apply_priority_change
        self._apply_se_owner_change = apply_se_owner_change
        self._apply_matrix_review_ack = apply_matrix_review_ack
        self._move_to_dev_action = move_to_dev_action
        self._return_to_se_action = return_to_se_action
        self._mark_in_progress_on_reply = mark_in_progress_on_reply
        self._open_resolve_modal = open_resolve_modal
        self._resolve_ticket = resolve_ticket
        self._reopen_ticket = reopen_ticket
        self._drop_ticket = drop_ticket
        self._open_add_org_modal = open_add_org_modal
        self._submit_add_affected_org = submit_add_affected_org
        self._open_reclassify_modal = open_reclassify_modal
        self._submit_reclassify = submit_reclassify
        self._create_article_from_faq = create_article_from_faq
        self._render_articles_board = render_articles_board
        self._open_set_deadline_modal = open_set_deadline_modal
        self._submit_deadline = submit_deadline
        self._open_set_stakeholder_modal = open_set_stakeholder_modal
        self._submit_set_stakeholder = submit_set_stakeholder
        self._toggle_reply_needed = toggle_reply_needed
        self._toggle_platform_wide = toggle_platform_wide
        self._render_tickets_board = render_tickets_board
        self._render_report = render_report
        self._se_timezone = se_timezone
        self._bolt_app = AsyncApp(
            token=config.bot_token,
            signing_secret=config.signing_secret,
        )
        self._client = AsyncWebClient(token=config.bot_token)
        self._gateway = SlackGateway(
            client=self._client,
            workspace_url=config.workspace_url,
        )
        # v1 handlers run regardless of the legacy flag.
        self._setup_v1_command()
        self._setup_v1_log_ticket_shortcut()
        self._setup_v1_link_ticket_shortcut()
        self._setup_v1_modals()
        self._setup_v1_log_check_detector()
        self._setup_v1_open_form_action()
        self._setup_v1_dedupe_actions()
        self._setup_v1_priority_actions()
        self._setup_v1_se_owner_action()
        self._setup_v1_matrix_review_actions()
        self._setup_v1_ticket_card_actions()
        self._setup_v1_add_affected_org_submission()
        self._setup_v1_reclassify_actions()
        self._setup_v1_articles()
        self._setup_v1_set_deadline()
        self._setup_v1_set_stakeholder()
        self._setup_v1_report()

    @property
    def integration_id(self) -> str:
        return INTEGRATION_ID

    # Slash commands that open the ticket-intake modal. `/log` is the primary,
    # readable name; `/l` is a one-keystroke shortcut. Both share one handler.
    _INTAKE_COMMANDS = ("/log", "/l")

    def _setup_v1_command(self) -> None:
        async def on_log_ticket(ack: AsyncAck, command: dict[str, object]) -> None:
            await ack()
            invoked = str(command.get("command", "/log"))
            trigger_id = str(command.get("trigger_id", ""))
            user_id = str(command.get("user_id", ""))
            channel_id = str(command.get("channel_id", "")) or None
            if not trigger_id or not user_id:
                logger.warning("%s invocation missing trigger_id/user_id", invoked)
                return
            await self._open_intake_modal.execute(
                trigger_id=trigger_id,
                invoker_user_id=user_id,
                invoker_channel_id=channel_id,
            )

        for cmd in self._INTAKE_COMMANDS:
            self._bolt_app.command(cmd)(on_log_ticket)

    def _setup_v1_log_ticket_shortcut(self) -> None:
        """`Log ticket` message shortcut (plan Part 4).

        Unlike the `/log` slash command (whose payload carries no `thread_ts`),
        a message-action payload includes the channel + message timestamps, so
        we can always link the resulting ticket back to the exact thread.
        """

        @self._bolt_app.shortcut("log_ticket_msg")
        async def on_log_ticket_shortcut(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            trigger_id = str(body.get("trigger_id") or "")
            user = body.get("user") or {}
            channel = body.get("channel") or {}
            message = body.get("message") or {}
            user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            channel_id = str(channel.get("id") or "")  # type: ignore[union-attr]
            # Reply to the thread root when the message is itself a reply.
            thread_ts = str(
                message.get("thread_ts") or message.get("ts") or ""  # type: ignore[union-attr]
            )
            message_text = str(message.get("text") or "")  # type: ignore[union-attr]
            if not trigger_id or not user_id or not channel_id or not thread_ts:
                logger.warning("log_ticket_msg shortcut missing required fields")
                return
            permalink = self._gateway.build_thread_link(channel_id, thread_ts)
            await self._open_intake_modal.execute(
                trigger_id=trigger_id,
                invoker_user_id=user_id,
                invoker_channel_id=channel_id,
                invoker_thread_ts=thread_ts,
                original_slack_link=permalink,
                prefill_description=_shortcut_prefill(message_text),
            )

    def _setup_v1_link_ticket_shortcut(self) -> None:
        """`Link to existing ticket` message shortcut (#userled-support).

        Attaches this thread to a live ticket instead of logging a duplicate, so
        it also gets the resolved reply + 🎫→✅ later. Only acts in the support
        channel (`OpenLinkModal` guards + ephemerally explains otherwise).
        """

        @self._bolt_app.shortcut("link_ticket_msg")
        async def on_link_ticket_shortcut(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            trigger_id = str(body.get("trigger_id") or "")
            user = body.get("user") or {}
            channel = body.get("channel") or {}
            message = body.get("message") or {}
            user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            channel_id = str(channel.get("id") or "")  # type: ignore[union-attr]
            thread_ts = str(
                message.get("thread_ts") or message.get("ts") or ""  # type: ignore[union-attr]
            )
            if not trigger_id or not user_id or not channel_id or not thread_ts:
                logger.warning("link_ticket_msg shortcut missing required fields")
                return
            await self._open_link_modal.execute(
                trigger_id=trigger_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                invoker_user_id=user_id,
            )

        @self._bolt_app.view(link_ticket.CALLBACK_ID)
        async def on_link_ticket_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                channel_id, thread_ts, target_ticket_id = parse_link_thread(view)  # type: ignore[arg-type]
            except ValueError as exc:
                logger.warning("link_ticket validation failed: %s", exc)
                return
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._submit_link_thread.execute(
                channel_id=channel_id,
                thread_ts=thread_ts,
                target_ticket_id=target_ticket_id,
                by_user_id=by_user_id,
            )

    def _setup_v1_modals(self) -> None:
        # DORMANT (2026-07-02): nothing opens the CSM intake modal anymore — the
        # intake split was retired (see OpenIntakeModal._choose_modal), so this
        # handler can never fire. Kept during a trial; REMOVE if we don't revert.
        @self._bolt_app.view(csm_intake.CALLBACK_ID)
        async def on_csm_intake_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                submission = parse_csm_intake(view)  # type: ignore[arg-type]
            except ValueError as exc:
                logger.warning("csm_intake validation failed: %s", exc)
                return
            view_id = str(view.get("id") or "") or None  # type: ignore[union-attr]
            reporter = str(user.get("id") or self._ryan_user_id)  # type: ignore[union-attr]
            original_link = str(view.get("private_metadata") or "") or None  # type: ignore[union-attr]
            await self._submit_ticket_form.from_csm_intake(
                submission,
                reporter_user_id=reporter,
                slack_view_id=view_id,
                original_slack_link=original_link,
            )

        @self._bolt_app.action(se_bug.ACTION_ORG)
        async def on_org_select(ack: AsyncAck, body: dict[str, object]) -> None:
            # Reveal/hide the inline new-org fields when the SE picks (or leaves)
            # the "➕ Create new org…" option. Fires because the Org input block
            # sets dispatch_action: true.
            await ack()
            await self._refresh_intake_view(body)

        @self._bolt_app.action(se_bug.ACTION_CAMPAIGN)
        async def on_campaign_select(ack: AsyncAck, body: dict[str, object]) -> None:
            # Reveal/hide the Campaign URL field when the SE toggles the
            # "Is part of campaign?" radio. Fires because that input block sets
            # dispatch_action: true.
            await ack()
            await self._refresh_intake_view(body)

        @self._bolt_app.view(se_bug.CALLBACK_ID)
        async def on_se_bug_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            view = body.get("view") or {}
            user = body.get("user") or {}
            reporter = str(user.get("id") or self._ryan_user_id)  # type: ignore[union-attr]
            try:
                submission = parse_se_bug(view)  # type: ignore[arg-type]
            except DeadlineTooSoonError as exc:
                # Surface on the deadline field instead of silently closing, so
                # the SE sees why and is pointed at the Urgent checkbox.
                await ack(response_action="errors", errors={exc.block: str(exc)})
                logger.info("se_bug deadline rejected: %s", exc)
                return
            except ValueError as exc:
                await ack()
                logger.warning("se_bug validation failed: %s", exc)
                return
            # "Create new org…" was picked: mint the org before logging so the
            # ticket links to a real row. Validation errors surface on the
            # offending field instead of silently closing the modal.
            if submission.create_new_org:
                try:
                    submission.org_id = await self._submit_ticket_form.create_org_from_intake(
                        name=submission.new_org_name or "",
                        channel_id=submission.new_org_channel_id or "",
                        owner_id=submission.new_org_owner_id or reporter,
                    )
                except OrgCreationError as exc:
                    block = {
                        "name": se_bug.BLOCK_NEW_ORG_NAME,
                        "channel": se_bug.BLOCK_NEW_ORG_CHANNEL,
                    }.get(exc.field, se_bug.BLOCK_NEW_ORG_NAME)
                    await ack(response_action="errors", errors={block: str(exc)})
                    logger.info("new-org creation rejected: %s", exc)
                    return
            await ack()
            view_id = str(view.get("id") or "") or None  # type: ignore[union-attr]
            original_link = str(view.get("private_metadata") or "") or None  # type: ignore[union-attr]
            await self._submit_ticket_form.from_se_bug(
                submission,
                reporter_user_id=reporter,
                slack_view_id=view_id,
                original_slack_link=original_link,
            )

    async def _refresh_intake_view(self, body: dict[str, object]) -> None:
        """Recompute both conditional-field toggles from the view's live state
        and re-render the intake modal. Shared by the Org-select and campaign
        block-actions so toggling one never wipes the other's revealed field."""
        view = body.get("view") or {}
        user = body.get("user") or {}
        view_id = str(view.get("id") or "")  # type: ignore[union-attr]
        if not view_id:
            return
        state_values = (view.get("state") or {}).get("values") or {}  # type: ignore[union-attr]
        await self._open_intake_modal.refresh_intake_view(
            view_id=view_id,
            show_new_org=se_bug.wants_new_org(state_values),
            show_campaign=se_bug.wants_campaign(state_values),
            invoker_user_id=str(user.get("id") or ""),  # type: ignore[union-attr]
            state_values=state_values,
            private_metadata=str(view.get("private_metadata") or ""),  # type: ignore[union-attr]
        )

    def _setup_v1_log_check_detector(self) -> None:
        """§3a customer-channel `log`/`check` detector + `app_mention` `log this`."""

        @self._bolt_app.event("message")
        async def on_message(event: dict[str, object]) -> None:
            subtype = event.get("subtype")
            if subtype in ("bot_message", "message_changed", "message_deleted"):
                return
            channel = str(event.get("channel", ""))
            user = str(event.get("user", ""))
            text = str(event.get("text", ""))
            ts = str(event.get("ts", ""))
            thread_ts = str(event.get("thread_ts", "") or ts)
            if not channel or not user or not ts:
                return
            # Customer-channel only — skip DMs (channel IDs starting with 'D').
            if channel.startswith("D"):
                return
            await self._detect_log_check.execute(
                channel_id=channel,
                thread_ts=thread_ts,
                sender_user_id=user,
                text=text,
            )
            # Independently: if the ticket's assigned SE just replied in the
            # thread it was raised from, advance New → In progress (+ Linear).
            await self._mark_in_progress_on_reply.execute(
                channel_id=channel,
                thread_ts=thread_ts,
                sender_user_id=user,
            )

        @self._bolt_app.event("app_mention")
        async def on_app_mention(event: dict[str, object]) -> None:
            text = str(event.get("text", ""))
            if not app_mention_triggers(text):
                return
            channel = str(event.get("channel", ""))
            user = str(event.get("user", ""))
            ts = str(event.get("ts", ""))
            thread_ts = str(event.get("thread_ts", "") or ts)
            if not channel or not user or not ts:
                return
            await self._detect_log_check.execute(
                channel_id=channel,
                thread_ts=thread_ts,
                sender_user_id=user,
                text=text,
            )

    def _setup_v1_open_form_action(self) -> None:
        @self._bolt_app.action(OPEN_SE_BUG_FROM_DETECTOR)
        async def on_open_form(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            actions = body.get("actions") or []
            user = body.get("user") or {}
            if not actions:
                return
            value = str(actions[0].get("value") or "")  # type: ignore[union-attr,index]
            if not value:
                return
            try:
                payload = decode_payload(value)
            except (ValueError, KeyError) as exc:
                logger.warning("Bad detector button payload: %s", exc)
                return
            trigger_id = str(body.get("trigger_id") or "")
            invoker = str(user.get("id") or "")  # type: ignore[union-attr]
            if not trigger_id or not invoker:
                return
            await self._open_intake_modal.execute(
                trigger_id=trigger_id,
                invoker_user_id=invoker,
                invoker_channel_id=None,  # invoked from a DM — force SE-bug modal
                invoker_thread_ts=None,
                prefill_description=payload.description,
                original_slack_link=payload.permalink,
            )

    def _setup_v1_dedupe_actions(self) -> None:
        @self._bolt_app.action(ACTION_MERGE_DEDUPE)
        async def on_merge(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            pending_id = _action_value_as_int(body)
            if pending_id is None:
                return
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._merge_into_existing.execute(pending_id=pending_id, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_CREATE_NEW_DEDUPE)
        async def on_create_new(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            pending_id = _action_value_as_int(body)
            if pending_id is None:
                return
            pending = await self._pending_dedupe_repo.get(pending_id)
            if pending is None:
                logger.warning("Create-new clicked on missing pending row %s", pending_id)
                return
            payload = StashedTicketPayload.from_json(pending.payload_json)
            await self._submit_ticket_form.proceed_create_from_pending(payload)
            await self._pending_dedupe_repo.delete(pending_id)

    def _setup_v1_priority_actions(self) -> None:
        @self._bolt_app.action(ACTION_SET_PRIORITY_PATTERN)
        async def on_set_priority(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            actions = body.get("actions") or []
            if not actions:
                return
            action = actions[0]  # type: ignore[index]
            # The card's `Set P-level` dropdown carries the payload in
            # `selected_option.value`; the override-DM/bump/P0 buttons carry it
            # in the bare `value`. Both route here via ACTION_SET_PRIORITY.
            selected = action.get("selected_option") or {}
            raw_value = str(selected.get("value") or action.get("value") or "")
            if not raw_value:
                return
            try:
                payload = PriorityChangePayload.decode(raw_value)
            except (ValueError, KeyError) as exc:
                logger.warning("Bad set-priority button payload: %s", exc)
                return
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._apply_priority_change.execute(payload, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_DISMISS_PRIO_DM)
        async def on_dismiss_prio_dm(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            # No-op — SE just clicked Skip. The DM remains in their thread; if
            # we wanted to chat.update the message we could here.
            _ = body

    def _setup_v1_se_owner_action(self) -> None:
        @self._bolt_app.action(ACTION_SET_SE_OWNER)
        async def on_set_se_owner(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            actions = body.get("actions") or []
            if not actions:
                return
            action = actions[0]  # type: ignore[index]
            # The card's SE-owner dropdown carries the payload in
            # `selected_option.value` (message static_selects, like priority).
            selected = action.get("selected_option") or {}
            raw_value = str(selected.get("value") or "")
            if not raw_value:
                return
            try:
                payload = SeOwnerChangePayload.decode(raw_value)
            except (ValueError, KeyError) as exc:
                logger.warning("Bad set-se-owner payload: %s", exc)
                return
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._apply_se_owner_change.execute(payload, by_user_id=by_user_id)

    def _setup_v1_matrix_review_actions(self) -> None:
        @self._bolt_app.action(ACTION_ACK_MATRIX_REVIEW)
        async def on_ack_review(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            _ = body
            await self._apply_matrix_review_ack.acknowledge()

        @self._bolt_app.action(ACTION_SNOOZE_MATRIX_REVIEW)
        async def on_snooze_review(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            _ = body
            await self._apply_matrix_review_ack.snooze_7d()

    def _setup_v1_ticket_card_actions(self) -> None:
        """Handlers for the six §2b ticket-card buttons (plan Chunk 9)."""

        @self._bolt_app.action(ACTION_MOVE_TO_DEV)
        async def on_move_to_dev(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._move_to_dev_action.execute(ticket_id=ticket_id, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_RETURN_TO_SE)
        async def on_return_to_se(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._return_to_se_action.execute(ticket_id=ticket_id, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_RESOLVED)
        async def on_resolved(ack: AsyncAck, body: dict[str, object]) -> None:
            # Resolving is terminal, so it captures reporting data first — the
            # click opens the resolve modal; ResolveTicket runs on submission.
            await ack()
            ticket_id = _action_value_as_int(body)
            trigger_id = str(body.get("trigger_id") or "")
            if ticket_id is None or not trigger_id:
                return
            await self._open_resolve_modal.execute(trigger_id=trigger_id, ticket_id=ticket_id)

        @self._bolt_app.view(resolve.CALLBACK_ID)
        async def on_resolve_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                ticket_id, resolution_type, pr_link = parse_resolve(view)  # type: ignore[arg-type]
            except ValueError as exc:
                # Only internal/malformed-payload errors reach here (the modal
                # itself enforces the required fields); surface on the PR-link
                # block as a fallback so the SE isn't left with a dead submit.
                await ack(
                    response_action="errors",
                    errors={resolve.BLOCK_PR_LINK: str(exc)},
                )
                logger.info("resolve validation rejected: %s", exc)
                return
            await ack()
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._resolve_ticket.execute(
                ticket_id=ticket_id,
                by_user_id=by_user_id,
                resolution_type=resolution_type,
                resolution_pr_link=pr_link,
            )

        @self._bolt_app.action(ACTION_REOPEN)
        async def on_reopen(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._reopen_ticket.execute(ticket_id=ticket_id, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_DROP)
        async def on_drop(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._drop_ticket.execute(ticket_id=ticket_id, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_ADD_AFFECTED_ORG)
        async def on_add_affected_org(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            trigger_id = str(body.get("trigger_id") or "")
            if ticket_id is None or not trigger_id:
                return
            await self._open_add_org_modal.execute(trigger_id=trigger_id, ticket_id=ticket_id)

        @self._bolt_app.action(ACTION_RECLASSIFY)
        async def on_reclassify(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            trigger_id = str(body.get("trigger_id") or "")
            if ticket_id is None or not trigger_id:
                return
            await self._open_reclassify_modal.execute(trigger_id=trigger_id, ticket_id=ticket_id)

    def _setup_v1_add_affected_org_submission(self) -> None:
        @self._bolt_app.view(add_affected_org.CALLBACK_ID)
        async def on_add_affected_org_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                ticket_id, org_id = parse_add_affected_org(view)  # type: ignore[arg-type]
            except ValueError as exc:
                logger.warning("add_affected_org validation failed: %s", exc)
                return
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._submit_add_affected_org.execute(
                ticket_id=ticket_id, org_id=org_id, by_user_id=by_user_id
            )

    def _setup_v1_reclassify_actions(self) -> None:
        @self._bolt_app.view(reclassify.CALLBACK_ID)
        async def on_reclassify_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                submission = parse_reclassify(view)  # type: ignore[arg-type]
            except ValueError as exc:
                # Surface the validation error to the modal so SE sees what
                # went wrong (Slack renders `response_action: errors` on the
                # offending block). Subtype-belongs-to-type mismatch is the
                # one a user could realistically hit, so route it back to
                # the subtype block.
                await ack(
                    response_action="errors",
                    errors={reclassify.BLOCK_NEW_SUBTYPE: str(exc)},
                )
                logger.info("reclassify validation rejected: %s", exc)
                return
            await ack()
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._submit_reclassify.execute(submission, by_user_id=by_user_id)

    def _setup_v1_articles(self) -> None:
        """`Needs article` button on FAQ cards + `/board articles` slash command."""

        @self._bolt_app.action(ACTION_NEEDS_ARTICLE)
        async def on_needs_article(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._create_article_from_faq.execute(ticket_id=ticket_id, by_user_id=by_user_id)

        @self._bolt_app.command("/board")
        async def on_board(ack: AsyncAck, command: dict[str, object]) -> None:
            await ack()
            text = str(command.get("text", "")).strip().lower()
            channel = str(command.get("channel_id", ""))
            user_id = str(command.get("user_id", ""))
            subcommand = text.split()[0] if text else ""
            if subcommand == "articles":
                blocks = await self._render_articles_board.execute()
                await self._gateway.send_ephemeral_blocks(
                    channel_id=channel,
                    user_id=user_id,
                    blocks=blocks,
                    text=":books: Articles board",
                )
                return
            if subcommand in ("", "tickets"):
                blocks = await self._render_tickets_board.execute()
                await self._gateway.send_ephemeral_blocks(
                    channel_id=channel,
                    user_id=user_id,
                    blocks=blocks,
                    text=":clipboard: Ticket board",
                )
                return
            await self._gateway.send_ephemeral(
                channel_id=channel,
                user_id=user_id,
                text=(
                    f":warning: Unknown `/board` subcommand `{subcommand}`. "
                    "Usage: `/board` (tickets) or `/board articles`."
                ),
            )

    def _setup_v1_set_deadline(self) -> None:
        @self._bolt_app.action(ACTION_SET_DEADLINE)
        async def on_set_deadline_click(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            trigger_id = str(body.get("trigger_id") or "")
            if ticket_id is None or not trigger_id:
                return
            await self._open_set_deadline_modal.execute(trigger_id=trigger_id, ticket_id=ticket_id)

        @self._bolt_app.view(set_deadline.CALLBACK_ID)
        async def on_set_deadline_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                ticket_id, picked = parse_set_deadline(view)  # type: ignore[arg-type]
            except ValueError as exc:
                logger.warning("set_deadline validation failed: %s", exc)
                return
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._submit_deadline.execute(
                ticket_id=ticket_id, deadline=picked, by_user_id=by_user_id
            )

        @self._bolt_app.action(ACTION_TOGGLE_REPLY_NEEDED)
        async def on_toggle_reply_needed(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._toggle_reply_needed.execute(ticket_id=ticket_id, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_TOGGLE_PLATFORM_WIDE)
        async def on_toggle_platform_wide(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._toggle_platform_wide.execute(ticket_id=ticket_id, by_user_id=by_user_id)

    def _setup_v1_set_stakeholder(self) -> None:
        @self._bolt_app.action(ACTION_SET_STAKEHOLDER)
        async def on_set_stakeholder_click(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            trigger_id = str(body.get("trigger_id") or "")
            if ticket_id is None or not trigger_id:
                return
            await self._open_set_stakeholder_modal.execute(
                trigger_id=trigger_id, ticket_id=ticket_id
            )

        @self._bolt_app.view(set_stakeholder.CALLBACK_ID)
        async def on_set_stakeholder_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                ticket_id, assignments = parse_set_stakeholder(view)  # type: ignore[arg-type]
            except ValueError as exc:
                logger.warning("set_stakeholder validation failed: %s", exc)
                return
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            await self._submit_set_stakeholder.execute(
                ticket_id=ticket_id, assignments=assignments, by_user_id=by_user_id
            )

    def _setup_v1_report(self) -> None:
        @self._bolt_app.command("/report")
        async def on_report(ack: AsyncAck, command: dict[str, object]) -> None:
            await ack()
            trigger_id = str(command.get("trigger_id") or "")
            channel_id = str(command.get("channel_id") or "")
            user_id = str(command.get("user_id") or "")
            if not trigger_id or not user_id:
                logger.warning("/report invocation missing trigger_id/user_id")
                return
            start, end = _default_report_range(self._se_timezone)
            view = report_range.build_view(
                channel_id=channel_id, user_id=user_id, start=start, end=end
            )
            await self._gateway.open_view(trigger_id, view)

        @self._bolt_app.view(report_range.CALLBACK_ID)
        async def on_report_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            view = body.get("view") or {}
            try:
                channel_id, user_id, start, end = parse_report_range(view)  # type: ignore[arg-type]
            except ValueError as exc:
                # Surface an inverted range on the end-date block so the SE can fix it.
                await ack(
                    response_action="errors",
                    errors={report_range.BLOCK_END: str(exc)},
                )
                logger.info("report range rejected: %s", exc)
                return
            await ack()
            blocks = await self._render_report.execute(start=start, end=end)
            await self._gateway.send_ephemeral_blocks(
                channel_id=channel_id,
                user_id=user_id,
                blocks=blocks,
                text=":sparkles: Product improvements",
            )

    def register_routes(self, app: FastAPI) -> None:
        handler = AsyncSlackRequestHandler(self._bolt_app)

        @app.post("/slack/events")
        async def slack_events(req: Request) -> Response:
            return await handler.handle(req)

    async def start(self) -> None:
        logger.info("UserledSupport Slack integration started")

    async def stop(self) -> None:
        pass
