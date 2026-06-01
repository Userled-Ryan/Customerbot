import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slack_sdk.web.async_client import AsyncWebClient

from customerbot.application.bot_state.sweeper import SweepEphemeralState
from customerbot.application.intake.dedupe import (
    FindDedupeCandidate,
    MergeIntoExisting,
    OfferDedupeChoice,
)
from customerbot.application.intake.detect_log_check import DetectLogCheck
from customerbot.application.intake.open_intake_modal import OpenIntakeModal
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import load_or_default
from customerbot.application.priority.monthly_review import (
    ApplyMatrixReviewAck,
    MonthlyMatrixReview,
)
from customerbot.application.priority.multi_customer_bump import MultiCustomerBumpCheck
from customerbot.application.priority.override import ApplyPriorityChange
from customerbot.application.priority.p0_scan import P0CandidateScan
from customerbot.application.sla.auto_close import AutoCloseAwaiting
from customerbot.application.sla.scan import SLAStateMachine
from customerbot.application.tracking.add_manual_ticket import AddManualTicket
from customerbot.application.tracking.build_summary import BuildSummary
from customerbot.application.tracking.handle_incoming_message import HandleIncomingMessage
from customerbot.application.tracking.send_daily_digest import SendDailyDigest
from customerbot.application.tracking.send_reminders import SendReminders
from customerbot.config import Settings
from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
    run_migrations,
)
from customerbot.data.repository import (
    SQLiteChannelCursorRepository,
    SQLiteConversationRepository,
    SQLiteKeywordRepository,
    SQLiteUserSettingsRepository,
)
from customerbot.data.repository.bot_state import (
    SQLiteChannelOrgCacheRepository,
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
    SQLitePendingPrioOverrideRepository,
    SQLitePendingReclassifySendRepository,
    SQLitePrioMatrixReviewStateRepository,
    SQLiteSLADMStateRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.integration.slack.gateway import SlackGateway
from customerbot.integration.slack.handler import SlackIntegration
from customerbot.integration.slack.modals import csm_intake as csm_intake_view
from customerbot.integration.slack.modals import se_bug as se_bug_view

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()  # type: ignore[call-arg]

# --- Infrastructure ---
database_url = database_url_from_path(settings.database_path)
engine = make_engine(database_url)
session_factory = make_session_factory(engine)
conversation_repo = SQLiteConversationRepository(session_factory=session_factory)
cursor_repo = SQLiteChannelCursorRepository(session_factory=session_factory)
keyword_repo = SQLiteKeywordRepository(session_factory=session_factory)
user_settings_repo = SQLiteUserSettingsRepository(session_factory=session_factory)

# --- v1 ticket-data repositories ---
ticket_repo = SQLiteTicketRepository(session_factory=session_factory)
org_repo = SQLiteOrgRepository(session_factory=session_factory)
event_log_repo = SQLiteEventLogRepository(session_factory=session_factory)

# --- v1 bot-state repositories (ephemeral / cache; not authoritative) ---
channel_org_cache_repo = SQLiteChannelOrgCacheRepository(session_factory=session_factory)
draft_form_repo = SQLiteDraftFormSessionRepository(session_factory=session_factory)
pending_dedupe_repo = SQLitePendingDedupeChoiceRepository(session_factory=session_factory)
pending_prio_repo = SQLitePendingPrioOverrideRepository(session_factory=session_factory)
pending_reclassify_repo = SQLitePendingReclassifySendRepository(session_factory=session_factory)
sla_dm_state_repo = SQLiteSLADMStateRepository(session_factory=session_factory)
sweep_ephemeral_state = SweepEphemeralState(
    drafts=draft_form_repo,
    pending_dedupe=pending_dedupe_repo,
    pending_prio=pending_prio_repo,
    pending_reclassify=pending_reclassify_repo,
)

# --- Slack Gateway ---
slack_client = AsyncWebClient(token=settings.slack.bot_token)
gateway = SlackGateway(client=slack_client, workspace_url=settings.slack.workspace_url)

# --- Use Cases ---
se_user_id = settings.se_user_id or settings.ryan_user_id
assert se_user_id is not None  # enforced by Settings validator

handle_incoming_message = HandleIncomingMessage(
    repo=conversation_repo,
    keywords=keyword_repo,
    messenger=gateway,
    ryan_user_id=se_user_id,
)
add_manual_ticket = AddManualTicket(
    repo=conversation_repo,
    messenger=gateway,
)
build_summary = BuildSummary(
    repo=conversation_repo,
    messenger=gateway,
    user_settings_repo=user_settings_repo,
    ryan_user_id=se_user_id,
    reminder_hours=settings.reminder_hours,
)
send_reminders = SendReminders(
    repo=conversation_repo,
    messenger=gateway,
    user_settings_repo=user_settings_repo,
    ryan_user_id=se_user_id,
    reminder_hours=settings.reminder_hours,
)
send_daily_digest = SendDailyDigest(
    repo=conversation_repo,
    messenger=gateway,
    user_settings_repo=user_settings_repo,
    ryan_user_id=se_user_id,
)

# --- v1 intake use cases ---
open_intake_modal = OpenIntakeModal(
    slack=gateway,
    orgs=org_repo,
    drafts=draft_form_repo,
    tech_assistance_channel_id=settings.tech_assistance_channel_id,
    csm_view_builder=csm_intake_view.build_view,
    se_view_builder=se_bug_view.build_view,
)
find_dedupe = FindDedupeCandidate(tickets=ticket_repo)
offer_dedupe = OfferDedupeChoice(slack=gateway, pending=pending_dedupe_repo)

# --- v1 priority pipeline ---
prio_matrix = load_or_default(settings.prio_matrix_path)
assign_priority = AssignPriority(matrix=prio_matrix, events=event_log_repo, slack=gateway)
apply_priority_change = ApplyPriorityChange(tickets=ticket_repo, events=event_log_repo)
multi_customer_bump_check = MultiCustomerBumpCheck(
    tickets=ticket_repo,
    slack=gateway,
    se_user_id=se_user_id,
    critical_path_features=settings.critical_path_features,
)
p0_candidate_scan = P0CandidateScan(
    tickets=ticket_repo,
    slack=gateway,
    se_user_id=se_user_id,
    cto_user_id=settings.cto_user_id,
    critical_path_features=settings.critical_path_features,
)
prio_matrix_review_state_repo = SQLitePrioMatrixReviewStateRepository(
    session_factory=session_factory
)
apply_matrix_review_ack = ApplyMatrixReviewAck(state=prio_matrix_review_state_repo)
monthly_matrix_review = MonthlyMatrixReview(
    slack=gateway,
    state=prio_matrix_review_state_repo,
    se_user_id=se_user_id,
    se_timezone=settings.se_timezone,
    prio_matrix_path=settings.prio_matrix_path,
)

# --- v1 SLA + auto-close (Chunk 8) ---
sla_state_machine = SLAStateMachine(
    tickets=ticket_repo,
    sla_state=sla_dm_state_repo,
    slack=gateway,
    se_user_id=se_user_id,
    sla_targets=settings.sla_targets,
)
auto_close_awaiting = AutoCloseAwaiting(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    sla_state=sla_dm_state_repo,
    slack=gateway,
    se_user_id=se_user_id,
)

merge_into_existing = MergeIntoExisting(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    pending=pending_dedupe_repo,
    slack=gateway,
    se_tickets_channel_id=settings.se_tickets_channel_id,
    bump_check=multi_customer_bump_check,
)
submit_ticket_form = SubmitTicketForm(
    slack=gateway,
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    drafts=draft_form_repo,
    find_dedupe=find_dedupe,
    offer_dedupe=offer_dedupe,
    assign_priority=assign_priority,
    se_user_id=se_user_id,
    se_tickets_channel_id=settings.se_tickets_channel_id,
)
detect_log_check = DetectLogCheck(
    slack=gateway,
    orgs=org_repo,
    channel_org_cache=channel_org_cache_repo,
    tickets=ticket_repo,
    bot_user_id=None,  # populated at runtime if needed; bot suppression already filters subtype
    internal_user_group_id=settings.internal_user_group_id,
)

# --- Slack Integration ---
slack_integration = SlackIntegration(
    config=settings.slack,
    handle_incoming_message=handle_incoming_message,
    build_summary=build_summary,
    add_manual_ticket=add_manual_ticket,
    conversation_repo=conversation_repo,
    keyword_repo=keyword_repo,
    user_settings_repo=user_settings_repo,
    ryan_user_id=se_user_id,
    open_intake_modal=open_intake_modal,
    submit_ticket_form=submit_ticket_form,
    detect_log_check=detect_log_check,
    merge_into_existing=merge_into_existing,
    pending_dedupe_repo=pending_dedupe_repo,
    apply_priority_change=apply_priority_change,
    apply_matrix_review_ack=apply_matrix_review_ack,
    legacy_commands_enabled=settings.legacy_commands_enabled,
)


def _log_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        logger.info("Background task cancelled")
    elif exc := task.exception():
        logger.error("Background task failed: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    run_migrations(database_url)
    logger.info("Database migrations applied")

    await slack_integration.start()

    background_tasks: list[asyncio.Task[None]] = []

    # Ephemeral-state sweeper runs unconditionally — drops expired
    # draft modal sessions (30 min) and stale pending-confirmation rows (7d).
    sweeper_task = asyncio.create_task(
        sweep_ephemeral_state.run_loop(interval_seconds=60),
        name="bot-state-sweeper",
    )
    sweeper_task.add_done_callback(_log_task_result)
    background_tasks.append(sweeper_task)

    # P0 candidate scan — every 30 min look for ≥5 orgs on a critical-path
    # feature hit within 6h, DM SE + CTO with [Set P0] buttons.
    p0_scan_task = asyncio.create_task(
        p0_candidate_scan.run_loop(interval_seconds=1800),
        name="p0-candidate-scan",
    )
    p0_scan_task.add_done_callback(_log_task_result)
    background_tasks.append(p0_scan_task)

    # Monthly prio-matrix review reminder (decision #4). Loop checks every
    # 5 min whether it's the 1st of the month at 09:00 SE-local-time and
    # whether we've already fired / been snoozed.
    monthly_review_task = asyncio.create_task(
        monthly_matrix_review.run_loop(interval_seconds=300),
        name="monthly-matrix-review",
    )
    monthly_review_task.add_done_callback(_log_task_result)
    background_tasks.append(monthly_review_task)

    # SLA state machine — every 15 min, fire green→amber / amber→red DMs.
    # Tickets in Awaiting customer are paused and skipped.
    sla_scan_task = asyncio.create_task(
        sla_state_machine.run_loop(interval_seconds=900),
        name="sla-state-machine",
    )
    sla_scan_task.add_done_callback(_log_task_result)
    background_tasks.append(sla_scan_task)

    # Auto-close — daily; closes awaiting>7d + fires CSM pre-close nudges
    # at the 7d/72h/24h marks.
    auto_close_task = asyncio.create_task(
        auto_close_awaiting.run_loop(interval_seconds=86400),
        name="auto-close-awaiting",
    )
    auto_close_task.add_done_callback(_log_task_result)
    background_tasks.append(auto_close_task)

    if settings.legacy_commands_enabled:
        reminder_task = asyncio.create_task(
            send_reminders.run_loop(interval_seconds=3600),
            name="reminder-loop",
        )
        reminder_task.add_done_callback(_log_task_result)
        background_tasks.append(reminder_task)

        digest_task = asyncio.create_task(
            send_daily_digest.run_loop(interval_seconds=60),
            name="digest-loop",
        )
        digest_task.add_done_callback(_log_task_result)
        background_tasks.append(digest_task)

    yield

    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await slack_integration.stop()
    await engine.dispose()
    logger.info("Shutdown complete")


# --- FastAPI App ---
api = FastAPI(lifespan=lifespan)
slack_integration.register_routes(api)


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}
