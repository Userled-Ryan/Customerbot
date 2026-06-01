from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient
from starlette.responses import Response

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
from customerbot.application.intake.open_intake_modal import OpenIntakeModal
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.application.intake.ticket_card import (
    ACTION_ADD_AFFECTED_ORG,
    ACTION_MOVE_TO_DEV,
    ACTION_NEEDS_ARTICLE,
    ACTION_RECLASSIFY,
    ACTION_REOPEN,
    ACTION_RESOLVED,
    ACTION_RESOLVED_HOTFIX,
    ACTION_SET_DEADLINE,
)
from customerbot.application.priority.actions import (
    ACTION_DISMISS_PRIO_DM,
    ACTION_SET_PRIORITY,
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
from customerbot.application.tracking.add_manual_ticket import AddManualTicket
from customerbot.application.tracking.articles import (
    CreateArticleFromFAQ,
    RenderArticlesBoard,
)
from customerbot.application.tracking.build_summary import BuildSummary
from customerbot.application.tracking.handle_incoming_message import HandleIncomingMessage
from customerbot.application.tracking.lane_handoff import MoveToDevAction
from customerbot.application.tracking.reclassify import (
    ACTION_DISMISS_RECLASSIFY,
    ACTION_SEND_RECLASSIFY,
    DismissReclassifyDraft,
    OpenReclassifyModal,
    SendReclassifyAlert,
    SubmitReclassifyDraft,
)
from customerbot.application.tracking.render_board import RenderTicketsBoard
from customerbot.application.tracking.reopen import ReopenTicket
from customerbot.application.tracking.resolve import ResolveTicket
from customerbot.application.tracking.set_deadline import OpenSetDeadlineModal, SubmitDeadline
from customerbot.config import SlackConfig
from customerbot.domain.bot_state.ports import PendingDedupeChoiceRepositoryPort
from customerbot.domain.tracking.entities import UserSettings
from customerbot.domain.tracking.ports import (
    ConversationRepositoryPort,
    KeywordRepositoryPort,
    UserSettingsRepositoryPort,
)
from customerbot.domain.tracking.value_objects import ConversationStatus
from customerbot.integration.slack.gateway import INTEGRATION_ID, SlackGateway
from customerbot.integration.slack.modals import (
    add_affected_org,
    csm_intake,
    reclassify,
    se_bug,
    set_deadline,
)
from customerbot.integration.slack.modals.submission_payload import (
    parse_add_affected_org,
    parse_csm_intake,
    parse_reclassify,
    parse_se_bug,
    parse_set_deadline,
)

logger = logging.getLogger(__name__)


def _action_value_as_int(body: dict[str, object]) -> int | None:
    actions = body.get("actions") or []
    if not actions:
        return None
    raw = actions[0].get("value")  # type: ignore[union-attr,index]
    try:
        return int(str(raw))
    except TypeError, ValueError:
        return None


def _split_keyword_and_category(text: str) -> tuple[str, str | None]:
    """Split `<word> as <category>` on the last ' as '; either side may contain spaces."""
    sep = " as "
    idx = text.rfind(sep)
    if idx == -1:
        return text.strip(), None
    word = text[:idx].strip()
    category = text[idx + len(sep) :].strip()
    return word, category or None


def _parse_hours(value: str) -> int | None:
    """Parse '4h', '48h', '2d' etc. into hours. Returns None if unparseable."""
    v = value.strip().lower()
    try:
        if v.endswith("h"):
            return int(v[:-1])
        if v.endswith("d"):
            return int(v[:-1]) * 24
        return int(v)
    except ValueError:
        return None


class SlackIntegration:
    """Slack integration: monitors channels, tracks conversations, and responds to commands."""

    def __init__(
        self,
        config: SlackConfig,
        handle_incoming_message: HandleIncomingMessage,
        build_summary: BuildSummary,
        add_manual_ticket: AddManualTicket,
        conversation_repo: ConversationRepositoryPort,
        keyword_repo: KeywordRepositoryPort,
        user_settings_repo: UserSettingsRepositoryPort,
        ryan_user_id: str,
        open_intake_modal: OpenIntakeModal,
        submit_ticket_form: SubmitTicketForm,
        detect_log_check: DetectLogCheck,
        merge_into_existing: MergeIntoExisting,
        pending_dedupe_repo: PendingDedupeChoiceRepositoryPort,
        apply_priority_change: ApplyPriorityChange,
        apply_matrix_review_ack: ApplyMatrixReviewAck,
        move_to_dev_action: MoveToDevAction,
        resolve_ticket: ResolveTicket,
        reopen_ticket: ReopenTicket,
        open_add_org_modal: OpenAddOrgModal,
        submit_add_affected_org: SubmitAddAffectedOrg,
        open_reclassify_modal: OpenReclassifyModal,
        submit_reclassify_draft: SubmitReclassifyDraft,
        send_reclassify_alert: SendReclassifyAlert,
        dismiss_reclassify_draft: DismissReclassifyDraft,
        create_article_from_faq: CreateArticleFromFAQ,
        render_articles_board: RenderArticlesBoard,
        open_set_deadline_modal: OpenSetDeadlineModal,
        submit_deadline: SubmitDeadline,
        render_tickets_board: RenderTicketsBoard,
        legacy_commands_enabled: bool = False,
    ) -> None:
        self._config = config
        self._handle_incoming_message = handle_incoming_message
        self._build_summary = build_summary
        self._add_manual_ticket = add_manual_ticket
        self._conversation_repo = conversation_repo
        self._keyword_repo = keyword_repo
        self._user_settings_repo = user_settings_repo
        self._ryan_user_id = ryan_user_id
        self._open_intake_modal = open_intake_modal
        self._submit_ticket_form = submit_ticket_form
        self._detect_log_check = detect_log_check
        self._merge_into_existing = merge_into_existing
        self._pending_dedupe_repo = pending_dedupe_repo
        self._apply_priority_change = apply_priority_change
        self._apply_matrix_review_ack = apply_matrix_review_ack
        self._move_to_dev_action = move_to_dev_action
        self._resolve_ticket = resolve_ticket
        self._reopen_ticket = reopen_ticket
        self._open_add_org_modal = open_add_org_modal
        self._submit_add_affected_org = submit_add_affected_org
        self._open_reclassify_modal = open_reclassify_modal
        self._submit_reclassify_draft = submit_reclassify_draft
        self._send_reclassify_alert = send_reclassify_alert
        self._dismiss_reclassify_draft = dismiss_reclassify_draft
        self._create_article_from_faq = create_article_from_faq
        self._render_articles_board = render_articles_board
        self._open_set_deadline_modal = open_set_deadline_modal
        self._submit_deadline = submit_deadline
        self._render_tickets_board = render_tickets_board
        self._legacy_commands_enabled = legacy_commands_enabled
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
        self._setup_v1_modals()
        self._setup_v1_log_check_detector()
        self._setup_v1_open_form_action()
        self._setup_v1_dedupe_actions()
        self._setup_v1_priority_actions()
        self._setup_v1_matrix_review_actions()
        self._setup_v1_ticket_card_actions()
        self._setup_v1_add_affected_org_submission()
        self._setup_v1_reclassify_actions()
        self._setup_v1_articles()
        self._setup_v1_set_deadline()
        if self._legacy_commands_enabled:
            self._setup_events()
            self._setup_commands()
        else:
            logger.info(
                "Legacy /csbot commands + app_mention summary disabled "
                "(CUSTOMERBOT_LEGACY_COMMANDS_ENABLED=false). "
                "v1 /log-ticket + modals are active."
            )

    @property
    def integration_id(self) -> str:
        return INTEGRATION_ID

    async def _get_settings(self, user_id: str) -> UserSettings:
        settings = await self._user_settings_repo.get(user_id)
        return settings if settings is not None else UserSettings(user_id=user_id)

    def _setup_events(self) -> None:
        @self._bolt_app.event("message")
        async def on_message(event: dict[str, object]) -> None:
            subtype = event.get("subtype")
            if subtype in ("bot_message", "message_changed", "message_deleted"):
                return

            user = str(event.get("user", ""))
            text = str(event.get("text", ""))
            channel = str(event.get("channel", ""))
            ts = str(event.get("ts", ""))
            thread_ts = str(event.get("thread_ts", "") or ts)

            if not user or not channel or not ts:
                return

            # DMs from Ryan: treat as a manual-ticket request if a Slack link is included.
            if channel.startswith("D"):
                if user != self._ryan_user_id:
                    return
                result = await self._add_manual_ticket.execute(text)
                await self._gateway.send_message(channel_id=channel, text=result.message)
                return

            await self._handle_incoming_message.execute(
                channel_id=channel,
                thread_ts=thread_ts,
                sender_user_id=user,
                text=text,
            )

        @self._bolt_app.event("app_mention")
        async def on_mention(event: dict[str, object]) -> None:
            channel = str(event.get("channel", ""))
            thread_ts = event.get("thread_ts")
            summary = await self._build_summary.execute()
            await self._gateway.send_message(
                channel_id=channel,
                text=summary,
                thread_ts=str(thread_ts) if thread_ts else None,
            )

    def _setup_commands(self) -> None:
        @self._bolt_app.command("/csbot")
        async def on_command(ack: AsyncAck, command: dict[str, object]) -> None:
            await ack()
            text_raw = str(command.get("text", "")).strip()
            text = text_raw.lower()
            channel = str(command.get("channel_id", ""))
            thread_ts = str(command.get("thread_ts", "") or "")
            user_id = str(command.get("user_id", ""))

            parts = text.split()
            parts_raw = text_raw.split()
            subcommand = parts[0] if parts else ""

            if subcommand == "summary" or text == "":
                summary = await self._build_summary.execute()
                await self._gateway.send_message(channel_id=channel, text=summary)
                return

            if subcommand == "close":
                args = parts[1:]

                # close all
                if args == ["all"]:
                    open_convs = await self._conversation_repo.find_open()
                    if not open_convs:
                        await self._gateway.send_message(
                            channel_id=channel,
                            text="ℹ️ No open tickets to close.",
                        )
                        return
                    for conv in open_convs:
                        await self._conversation_repo.update_status(
                            conv.channel_id, conv.thread_ts, ConversationStatus.CLOSED
                        )
                    await self._conversation_repo.repack_ticket_numbers()
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=f"✅ Closed all {len(open_convs)} open ticket(s).",
                    )
                    return

                # close 1 2 3 ...
                if args and all(a.isdigit() for a in args):
                    closed, not_found = [], []
                    for arg in args:
                        ticket_id = int(arg)
                        conv = await self._conversation_repo.find_by_id(ticket_id)
                        if conv is None:
                            not_found.append(ticket_id)
                        else:
                            await self._conversation_repo.update_status(
                                conv.channel_id, conv.thread_ts, ConversationStatus.CLOSED
                            )
                            label = conv.channel_name or conv.channel_id
                            closed.append((ticket_id, label))

                    if closed:
                        await self._conversation_repo.repack_ticket_numbers()
                    lines = []
                    if closed:
                        closed_str = ", ".join(f"`#{tid}` (#{lbl})" for tid, lbl in closed)
                        lines.append(f"✅ Closed: {closed_str}")
                    if not_found:
                        nf_str = ", ".join(f"`#{tid}`" for tid in not_found)
                        lines.append(f"⚠️ Not found: {nf_str}")
                    await self._gateway.send_message(channel_id=channel, text="\n".join(lines))
                    return

                # Fallback: close the current thread (when run inside a tracked thread)
                target_ts = thread_ts or None
                if not target_ts:
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=(
                            "⚠️ Usage: `/csbot close <id> [id ...]` or `/csbot close all`"
                            " — find IDs via `/csbot summary`."
                        ),
                    )
                    return
                conv = await self._conversation_repo.find_by_thread(channel, target_ts)
                if conv is None:
                    await self._gateway.send_ephemeral(
                        channel_id=channel,
                        user_id=user_id,
                        text="ℹ️ No tracked conversation found for this thread.",
                    )
                    return
                await self._conversation_repo.update_status(
                    channel, target_ts, ConversationStatus.CLOSED
                )
                await self._conversation_repo.repack_ticket_numbers()
                # Ephemeral so customers in the thread don't see a bot confirmation
                await self._gateway.send_ephemeral(
                    channel_id=channel,
                    user_id=user_id,
                    text=f"✅ Closed ticket `#{conv.ticket_number}`.",
                )
                return

            if subcommand == "keyword":
                args = parts[1:]
                action = args[0] if args else ""

                if action == "list":
                    entries = await self._keyword_repo.list_all()
                    if not entries:
                        msg = (
                            "ℹ️ No keywords configured"
                            " — no tickets will be created until you add one."
                        )
                    else:
                        lines = ["*Tracked keywords*"]
                        for word, category in entries:
                            if category:
                                lines.append(f"• `{word}` → `{category}`")
                            else:
                                lines.append(f"• `{word}`")
                        msg = "\n".join(lines)
                    await self._gateway.send_message(channel_id=channel, text=msg)
                    return

                if action == "add" and len(args) >= 2:
                    rest = " ".join(args[1:]).strip()
                    # Parse "<word> as <category>" — split on the LAST " as " so
                    # the keyword phrase can itself contain " as ".
                    word, category = _split_keyword_and_category(rest)
                    if not word:
                        await self._gateway.send_message(
                            channel_id=channel,
                            text="⚠️ Usage: `/csbot keyword add <word> [as <category>]`",
                        )
                        return
                    await self._keyword_repo.add(word, category)
                    if category:
                        msg = f"✅ Saved keyword `{word.lower()}` → `{category.lower()}`."
                    else:
                        msg = f"✅ Saved keyword `{word.lower()}` (no category)."
                    await self._gateway.send_message(channel_id=channel, text=msg)
                    return

                if action == "remove" and len(args) >= 2:
                    word = " ".join(args[1:]).strip()
                    removed = await self._keyword_repo.remove(word)
                    msg = (
                        f"✅ Removed keyword `{word.lower()}`."
                        if removed
                        else f"⚠️ Keyword `{word.lower()}` not found."
                    )
                    await self._gateway.send_message(channel_id=channel, text=msg)
                    return

                await self._gateway.send_message(
                    channel_id=channel,
                    text=(
                        "⚠️ Usage: `/csbot keyword add <word> [as <category>]`, "
                        "`/csbot keyword remove <word>`, or `/csbot keyword list`."
                    ),
                )
                return

            # --- timezone ---
            if subcommand == "timezone":
                settings = await self._get_settings(self._ryan_user_id)
                if len(parts_raw) < 2:
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=(
                            f"🌍 Current timezone: `{settings.timezone}`\n"
                            "Set it with `/csbot timezone <tz>`"
                            " e.g. `/csbot timezone America/New_York`"
                        ),
                    )
                    return
                tz_name = parts_raw[1]
                try:
                    ZoneInfo(tz_name)
                except ZoneInfoNotFoundError:
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=(
                            f"⚠️ Unknown timezone `{tz_name}`. Use IANA format,"
                            " e.g. `America/New_York`, `Europe/London`,"
                            " `America/Los_Angeles`."
                        ),
                    )
                    return
                settings.timezone = tz_name
                # Reset digest dates so digests fire at the new timezone's times
                settings.last_morning_digest_date = None
                settings.last_evening_digest_date = None
                await self._user_settings_repo.save(settings)
                await self._gateway.send_message(
                    channel_id=channel,
                    text=(
                        f"✅ Timezone set to `{tz_name}`."
                        f" Daily digests will now fire at 9am and 5pm {tz_name}."
                    ),
                )
                return

            # --- reminder ---
            if subcommand == "reminder":
                settings = await self._get_settings(self._ryan_user_id)
                args = parts[1:]

                # reminder #<id> <interval>  — per-ticket override
                if len(args) == 2 and args[0].startswith("#") and args[0][1:].isdigit():
                    ticket_id = int(args[0][1:])
                    hours = _parse_hours(args[1])
                    if hours is None or hours < 1:
                        await self._gateway.send_message(
                            channel_id=channel,
                            text=(
                                "⚠️ Usage: `/csbot reminder #<id> <interval>`"
                                " e.g. `/csbot reminder #3 4h`"
                            ),
                        )
                        return
                    conv = await self._conversation_repo.find_by_id(ticket_id)
                    if conv is None:
                        await self._gateway.send_message(
                            channel_id=channel,
                            text=f"⚠️ Ticket `#{ticket_id}` not found.",
                        )
                        return
                    await self._conversation_repo.update_reminder_interval(ticket_id, hours)
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=f"✅ Ticket `#{ticket_id}` will now remind every `{hours}h`.",
                    )
                    return

                # reminder #<id>  — show current interval for a ticket
                if len(args) == 1 and args[0].startswith("#") and args[0][1:].isdigit():
                    ticket_id = int(args[0][1:])
                    conv = await self._conversation_repo.find_by_id(ticket_id)
                    if conv is None:
                        await self._gateway.send_message(
                            channel_id=channel,
                            text=f"⚠️ Ticket `#{ticket_id}` not found.",
                        )
                        return
                    interval = conv.reminder_interval_hours
                    default_h = settings.default_reminder_hours
                    if interval is None:
                        msg = (
                            f"ℹ️ Ticket `#{ticket_id}` uses the default"
                            f" reminder interval (`{default_h}h`)."
                        )
                    else:
                        msg = (
                            f"ℹ️ Ticket `#{ticket_id}` reminds every"
                            f" `{interval}h` (default is `{default_h}h`)."
                        )
                    await self._gateway.send_message(channel_id=channel, text=msg)
                    return

                # reminder <interval>  — set default
                if len(args) == 1:
                    hours = _parse_hours(args[0])
                    if hours is None or hours < 1:
                        await self._gateway.send_message(
                            channel_id=channel,
                            text=(
                                "⚠️ Usage: `/csbot reminder <interval>`"
                                " e.g. `/csbot reminder 48h` or `/csbot reminder 2d`"
                            ),
                        )
                        return
                    settings.default_reminder_hours = hours
                    await self._user_settings_repo.save(settings)
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=(
                            f"✅ Default reminder interval set to `{hours}h`."
                            " Tickets without a custom interval will use this."
                        ),
                    )
                    return

                # no args — show current default
                await self._gateway.send_message(
                    channel_id=channel,
                    text=(
                        f"⏰ Default reminder interval: `{settings.default_reminder_hours}h`\n"
                        "• `/csbot reminder <interval>` — change default (e.g. `48h`, `2d`)\n"
                        "• `/csbot reminder #<id> <interval>` — set per-ticket interval\n"
                        "• `/csbot reminder #<id>` — show a ticket's current interval"
                    ),
                )
                return

            # --- alerts ---
            if subcommand == "alerts":
                settings = await self._get_settings(self._ryan_user_id)
                args = parts[1:]

                if not args:
                    status = "on ✅" if settings.daily_digest_enabled else "off ⛔"
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=(
                            f"🔔 Daily digests: {status}\n"
                            f"• 9am digest: summary of all open tickets\n"
                            f"• 5pm digest: new tickets today + overdue tickets\n"
                            f"• Timezone: `{settings.timezone}`\n"
                            "Use `/csbot alerts on` or `/csbot alerts off` to toggle."
                        ),
                    )
                    return

                if args[0] == "on":
                    settings.daily_digest_enabled = True
                    await self._user_settings_repo.save(settings)
                    await self._gateway.send_message(
                        channel_id=channel, text="✅ Daily digests enabled."
                    )
                    return

                if args[0] == "off":
                    settings.daily_digest_enabled = False
                    await self._user_settings_repo.save(settings)
                    await self._gateway.send_message(
                        channel_id=channel, text="⛔ Daily digests disabled."
                    )
                    return

                await self._gateway.send_message(
                    channel_id=channel,
                    text="⚠️ Usage: `/csbot alerts`, `/csbot alerts on`, or `/csbot alerts off`",
                )
                return

            # --- settings ---
            if subcommand == "settings":
                settings = await self._get_settings(self._ryan_user_id)
                digest_status = "on ✅" if settings.daily_digest_enabled else "off ⛔"
                await self._gateway.send_message(
                    channel_id=channel,
                    text=(
                        "*⚙️ CustomerBot settings*\n"
                        f"• Timezone: `{settings.timezone}`\n"
                        f"• Default reminder interval: `{settings.default_reminder_hours}h`\n"
                        f"• Daily digests: {digest_status}\n"
                        "\n_Change with `/csbot timezone`, `/csbot reminder`, `/csbot alerts`_"
                    ),
                )
                return

            help_text = (
                "*CustomerBot commands*\n"
                "• `/csbot` or `/csbot summary` — show open tickets with IDs\n"
                "• `/csbot close <id>` — close a ticket by ID (works from anywhere)\n"
                "• `/csbot close <id> <id> ...` — close multiple tickets at once\n"
                "• `/csbot close all` — close all open tickets\n"
                "• `/csbot close` — close the current thread's ticket (when used inside a thread)\n"
                "• `/csbot keyword add <word> [as <category>]`"
                " — track a keyword and optionally tag matching tickets\n"
                "• `/csbot keyword remove <word>` — stop tracking a keyword\n"
                "• `/csbot keyword list` — list all tracked keywords\n"
                "• `/csbot timezone <tz>` — set your timezone (e.g. `America/New_York`)\n"
                "• `/csbot reminder <interval>` — set default interval (e.g. `48h`, `2d`)\n"
                "• `/csbot reminder #<id> <interval>` — set interval for a specific ticket\n"
                "• `/csbot alerts on/off` — toggle 9am/5pm daily digest alerts\n"
                "• `/csbot settings` — show your current configuration\n"
                "_DM me a Slack thread link to manually open a ticket for that thread._"
            )
            await self._gateway.send_message(channel_id=channel, text=help_text)

    def _setup_v1_command(self) -> None:
        @self._bolt_app.command("/log-ticket")
        async def on_log_ticket(ack: AsyncAck, command: dict[str, object]) -> None:
            await ack()
            trigger_id = str(command.get("trigger_id", ""))
            user_id = str(command.get("user_id", ""))
            channel_id = str(command.get("channel_id", "")) or None
            if not trigger_id or not user_id:
                logger.warning("/log-ticket invocation missing trigger_id/user_id")
                return
            await self._open_intake_modal.execute(
                trigger_id=trigger_id,
                invoker_user_id=user_id,
                invoker_channel_id=channel_id,
            )

    def _setup_v1_modals(self) -> None:
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

        @self._bolt_app.view(se_bug.CALLBACK_ID)
        async def on_se_bug_submit(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            view = body.get("view") or {}
            user = body.get("user") or {}
            try:
                submission = parse_se_bug(view)  # type: ignore[arg-type]
            except ValueError as exc:
                logger.warning("se_bug validation failed: %s", exc)
                return
            view_id = str(view.get("id") or "") or None  # type: ignore[union-attr]
            reporter = str(user.get("id") or self._ryan_user_id)  # type: ignore[union-attr]
            original_link = str(view.get("private_metadata") or "") or None  # type: ignore[union-attr]
            await self._submit_ticket_form.from_se_bug(
                submission,
                reporter_user_id=reporter,
                slack_view_id=view_id,
                original_slack_link=original_link,
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
        @self._bolt_app.action(ACTION_SET_PRIORITY)
        async def on_set_priority(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            actions = body.get("actions") or []
            if not actions:
                return
            raw_value = str(actions[0].get("value") or "")  # type: ignore[union-attr,index]
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

        @self._bolt_app.action(ACTION_RESOLVED)
        async def on_resolved(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._resolve_ticket.execute(
                ticket_id=ticket_id, by_user_id=by_user_id, via_hotfix=False
            )

        @self._bolt_app.action(ACTION_RESOLVED_HOTFIX)
        async def on_resolved_hotfix(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            ticket_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if ticket_id is None:
                return
            await self._resolve_ticket.execute(
                ticket_id=ticket_id, by_user_id=by_user_id, via_hotfix=True
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
            await self._submit_reclassify_draft.execute(submission, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_SEND_RECLASSIFY)
        async def on_send_reclassify(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            pending_id = _action_value_as_int(body)
            user = body.get("user") or {}
            by_user_id = str(user.get("id") or "")  # type: ignore[union-attr]
            if pending_id is None:
                return
            await self._send_reclassify_alert.execute(pending_id=pending_id, by_user_id=by_user_id)

        @self._bolt_app.action(ACTION_DISMISS_RECLASSIFY)
        async def on_dismiss_reclassify(ack: AsyncAck, body: dict[str, object]) -> None:
            await ack()
            pending_id = _action_value_as_int(body)
            if pending_id is None:
                return
            await self._dismiss_reclassify_draft.execute(pending_id=pending_id)

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

    def register_routes(self, app: FastAPI) -> None:
        handler = AsyncSlackRequestHandler(self._bolt_app)

        @app.post("/slack/events")
        async def slack_events(req: Request) -> Response:
            return await handler.handle(req)

    async def start(self) -> None:
        logger.info("CustomerBot Slack integration started")

    async def stop(self) -> None:
        pass
