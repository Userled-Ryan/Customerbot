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
from customerbot.application.intake.link_thread import OpenLinkModal, SubmitLinkThread
from customerbot.application.intake.open_intake_modal import OpenIntakeModal
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.application.linear.inbound import LinearInboundHandler
from customerbot.application.linear.reconcile import ReconcileLinear
from customerbot.application.linear.sync import LinearSync
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import load_or_default
from customerbot.application.priority.monthly_review import (
    ApplyMatrixReviewAck,
    MonthlyMatrixReview,
)
from customerbot.application.priority.multi_customer_bump import MultiCustomerBumpCheck
from customerbot.application.priority.override import ApplyPriorityChange
from customerbot.application.priority.p0_scan import P0CandidateScan
from customerbot.application.sla.scan import SLAStateMachine
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
from customerbot.application.tracking.open_tickets_digest import OpenTicketsDigestJob
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
from customerbot.config import Settings
from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
    run_migrations,
)
from customerbot.data.repository import SQLiteChannelCursorRepository
from customerbot.data.repository.articles import SQLiteArticleRepository
from customerbot.data.repository.bot_state import (
    SQLiteChannelOrgCacheRepository,
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
    SQLitePendingPrioOverrideRepository,
    SQLitePrioMatrixReviewStateRepository,
    SQLiteSLADMStateRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.integration.anthropic.summarizer import AnthropicReportSummarizer
from customerbot.integration.linear.gateway import LinearGateway, NoOpLinearGateway
from customerbot.integration.linear.webhook import LinearWebhook
from customerbot.integration.slack.gateway import SlackGateway
from customerbot.integration.slack.handler import SlackIntegration
from customerbot.integration.slack.modals import add_affected_org as add_affected_org_view
from customerbot.integration.slack.modals import csm_intake as csm_intake_view
from customerbot.integration.slack.modals import link_ticket as link_ticket_view
from customerbot.integration.slack.modals import reclassify as reclassify_view
from customerbot.integration.slack.modals import resolve as resolve_view
from customerbot.integration.slack.modals import se_bug as se_bug_view
from customerbot.integration.slack.modals import set_deadline as set_deadline_view
from customerbot.integration.slack.modals import set_stakeholder as set_stakeholder_view
from customerbot.integration.webhooks.in_app_bug import InAppBugWebhook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()  # type: ignore[call-arg]

# --- Infrastructure ---
database_url = database_url_from_path(settings.database_path)
engine = make_engine(database_url)
session_factory = make_session_factory(engine)
cursor_repo = SQLiteChannelCursorRepository(session_factory=session_factory)

# --- v1 ticket-data repositories ---
ticket_repo = SQLiteTicketRepository(session_factory=session_factory)
org_repo = SQLiteOrgRepository(session_factory=session_factory)
event_log_repo = SQLiteEventLogRepository(session_factory=session_factory)
article_repo = SQLiteArticleRepository(session_factory=session_factory)

# --- v1 bot-state repositories (ephemeral / cache; not authoritative) ---
channel_org_cache_repo = SQLiteChannelOrgCacheRepository(session_factory=session_factory)
draft_form_repo = SQLiteDraftFormSessionRepository(session_factory=session_factory)
pending_dedupe_repo = SQLitePendingDedupeChoiceRepository(session_factory=session_factory)
pending_prio_repo = SQLitePendingPrioOverrideRepository(session_factory=session_factory)
sla_dm_state_repo = SQLiteSLADMStateRepository(session_factory=session_factory)
sweep_ephemeral_state = SweepEphemeralState(
    drafts=draft_form_repo,
    pending_dedupe=pending_dedupe_repo,
    pending_prio=pending_prio_repo,
)

# --- Slack Gateway ---
slack_client = AsyncWebClient(token=settings.slack.bot_token)
gateway = SlackGateway(client=slack_client, workspace_url=settings.slack.workspace_url)

# --- Use Cases ---
se_user_id = settings.se_user_id or settings.ryan_user_id
assert se_user_id is not None  # enforced by Settings validator

# --- Linear mirror (v1.5) ---
# Best-effort outbound mirror + inbound webhook. When unconfigured, a NoOp
# gateway keeps every downstream wiring/test path working with Linear off.
linear_enabled = settings.linear is not None
if settings.linear is not None:
    linear_gateway: LinearGateway | NoOpLinearGateway = LinearGateway(
        api_token=settings.linear.api_token,
        team_id=settings.linear.team_id,
        project_id=settings.linear.project_id,
        workflow_states=settings.linear.workflow_states,
        actor_id=settings.linear.actor_id,
        timeout_seconds=settings.linear.http_timeout_seconds,
    )
else:
    linear_gateway = NoOpLinearGateway()
linear_sync = LinearSync(linear=linear_gateway, tickets=ticket_repo, orgs=org_repo)

# --- v1 intake use cases ---
open_intake_modal = OpenIntakeModal(
    slack=gateway,
    orgs=org_repo,
    drafts=draft_form_repo,
    tech_assistance_channel_id=settings.tech_assistance_channel_id,
    product_channel_id=settings.product_channel_id,
    gleap_channel_id=settings.gleap_channel_id,
    csm_view_builder=csm_intake_view.build_view,
    se_view_builder=se_bug_view.build_view,
)
find_dedupe = FindDedupeCandidate(tickets=ticket_repo)
offer_dedupe = OfferDedupeChoice(slack=gateway, pending=pending_dedupe_repo)

# --- v1 priority pipeline ---
prio_matrix = load_or_default(settings.prio_matrix_path)
assign_priority = AssignPriority(matrix=prio_matrix, events=event_log_repo)
apply_priority_change = ApplyPriorityChange(
    tickets=ticket_repo,
    events=event_log_repo,
    slack=gateway,
    orgs=org_repo,
    linear=linear_sync,
)
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

# --- v1 SLA state machine (Chunk 8) ---
sla_state_machine = SLAStateMachine(
    tickets=ticket_repo,
    sla_state=sla_dm_state_repo,
    sla_targets=settings.sla_targets,
)

merge_into_existing = MergeIntoExisting(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    pending=pending_dedupe_repo,
    slack=gateway,
    se_tickets_channel_id=settings.se_tickets_channel_id,
    bump_check=multi_customer_bump_check,
    support_channel_ids=settings.support_thread_channel_ids,
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
    tech_assistance_channel_id=settings.tech_assistance_channel_id,
    support_channel_ids=settings.support_thread_channel_ids,
    linear=linear_sync,
)
open_link_modal = OpenLinkModal(
    slack=gateway,
    tickets=ticket_repo,
    orgs=org_repo,
    view_builder=link_ticket_view.build_view,
    support_channel_ids=settings.support_thread_channel_ids,
)
submit_link_thread = SubmitLinkThread(
    slack=gateway,
    tickets=ticket_repo,
)
in_app_bug_webhook = InAppBugWebhook(
    submit_ticket_form=submit_ticket_form,
    inapp_webhook_secret=settings.inapp_webhook_secret,
)
detect_log_check = DetectLogCheck(
    slack=gateway,
    orgs=org_repo,
    channel_org_cache=channel_org_cache_repo,
    tickets=ticket_repo,
    bot_user_id=None,  # populated at runtime if needed; bot suppression already filters subtype
    internal_user_group_id=settings.internal_user_group_id,
    se_user_id=se_user_id,
)

# --- v1 Chunk-9 lifecycle handlers (interactive ticket-card buttons) ---
move_to_dev_action = MoveToDevAction(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    slack=gateway,
    support_handle=settings.support_handle,
    linear=linear_sync,
)
return_to_se_action = ReturnToSEAction(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    slack=gateway,
    support_handle=settings.support_handle,
    linear=linear_sync,
)
open_resolve_modal = OpenResolveModal(
    slack=gateway,
    tickets=ticket_repo,
    view_builder=resolve_view.build_view,
)
resolve_ticket = ResolveTicket(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    slack=gateway,
    se_user_id=se_user_id,
    linear=linear_sync,
    support_channel_ids=settings.support_thread_channel_ids,
)
reopen_ticket = ReopenTicket(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    slack=gateway,
    se_user_id=se_user_id,
    linear=linear_sync,
)
drop_ticket = DropTicket(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    slack=gateway,
    linear=linear_sync,
)
open_add_org_modal = OpenAddOrgModal(
    slack=gateway,
    orgs=org_repo,
    tickets=ticket_repo,
    view_builder=add_affected_org_view.build_view,
)
submit_add_affected_org = SubmitAddAffectedOrg(
    slack=gateway,
    tickets=ticket_repo,
    orgs=org_repo,
    bump_check=multi_customer_bump_check,
)
open_reclassify_modal = OpenReclassifyModal(
    slack=gateway,
    tickets=ticket_repo,
    view_builder=reclassify_view.build_view,
)
submit_reclassify = SubmitReclassify(
    slack=gateway,
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    support_handle=settings.support_handle,
    support_ping_channel_id=settings.support_ping_channel_id,
    linear=linear_sync,
)

# --- v1 articles workflow (Chunk 12) ---
create_article_from_faq = CreateArticleFromFAQ(
    tickets=ticket_repo,
    articles=article_repo,
    orgs=org_repo,
    slack=gateway,
    se_user_id=se_user_id,
)
render_articles_board = RenderArticlesBoard(
    articles=article_repo,
    tickets=ticket_repo,
)
open_set_deadline_modal = OpenSetDeadlineModal(
    slack=gateway,
    tickets=ticket_repo,
    view_builder=set_deadline_view.build_view,
)
submit_deadline = SubmitDeadline(
    slack=gateway,
    tickets=ticket_repo,
    orgs=org_repo,
)
open_set_stakeholder_modal = OpenSetStakeholderModal(
    slack=gateway,
    tickets=ticket_repo,
    orgs=org_repo,
    view_builder=set_stakeholder_view.build_view,
)
submit_set_stakeholder = SubmitSetStakeholder(
    slack=gateway,
    tickets=ticket_repo,
    orgs=org_repo,
)
toggle_reply_needed = ToggleReplyNeeded(
    slack=gateway,
    tickets=ticket_repo,
    orgs=org_repo,
)
toggle_platform_wide = TogglePlatformWide(
    slack=gateway,
    tickets=ticket_repo,
    orgs=org_repo,
)

# --- v1 weekly digest + on-demand board (Chunk 13) ---
open_tickets_digest_job = OpenTicketsDigestJob(
    tickets=ticket_repo,
    slack=gateway,
    se_user_id=se_user_id,
    se_timezone=settings.se_timezone,
    workspace_url=settings.slack.workspace_url,
)
render_tickets_board = RenderTicketsBoard(
    tickets=ticket_repo,
    orgs=org_repo,
    workspace_url=settings.slack.workspace_url,
)

# --- /report product-improvement summary ---
# Optional LLM narrative; when Anthropic is unconfigured the summariser is None
# and RenderReport uses its deterministic template.
report_summarizer = (
    AnthropicReportSummarizer(
        api_key=settings.anthropic.api_key,
        model=settings.anthropic.model,
    )
    if settings.anthropic is not None
    else None
)
render_report = RenderReport(tickets=ticket_repo, summarizer=report_summarizer)

# --- Linear inbound + reconcile (v1.5) ---
# Inbound applies a dev's Linear change back into customerbot (no desync) and
# notifies the SE + stakeholders; it routes transitions through resolve/drop
# with sync_to_linear=False so it never echoes a write back to Linear.
linear_inbound = LinearInboundHandler(
    tickets=ticket_repo,
    events=event_log_repo,
    orgs=org_repo,
    slack=gateway,
    drop_ticket=drop_ticket,
    se_user_id=se_user_id,
    workspace_url=settings.slack.workspace_url,
    actor_id=settings.linear.actor_id if settings.linear else None,
)
linear_webhook = LinearWebhook(
    inbound=linear_inbound,
    tickets=ticket_repo,
    webhook_secret=settings.linear.webhook_secret if settings.linear else None,
)
reconcile_linear = ReconcileLinear(
    tickets=ticket_repo,
    linear=linear_gateway,
    sync=linear_sync,
    inbound=linear_inbound,
)

# --- Slack Integration ---
slack_integration = SlackIntegration(
    config=settings.slack,
    ryan_user_id=se_user_id,
    open_intake_modal=open_intake_modal,
    submit_ticket_form=submit_ticket_form,
    detect_log_check=detect_log_check,
    open_link_modal=open_link_modal,
    submit_link_thread=submit_link_thread,
    merge_into_existing=merge_into_existing,
    pending_dedupe_repo=pending_dedupe_repo,
    apply_priority_change=apply_priority_change,
    apply_matrix_review_ack=apply_matrix_review_ack,
    move_to_dev_action=move_to_dev_action,
    return_to_se_action=return_to_se_action,
    open_resolve_modal=open_resolve_modal,
    resolve_ticket=resolve_ticket,
    reopen_ticket=reopen_ticket,
    drop_ticket=drop_ticket,
    open_add_org_modal=open_add_org_modal,
    submit_add_affected_org=submit_add_affected_org,
    open_reclassify_modal=open_reclassify_modal,
    submit_reclassify=submit_reclassify,
    create_article_from_faq=create_article_from_faq,
    render_articles_board=render_articles_board,
    open_set_deadline_modal=open_set_deadline_modal,
    submit_deadline=submit_deadline,
    open_set_stakeholder_modal=open_set_stakeholder_modal,
    submit_set_stakeholder=submit_set_stakeholder,
    toggle_reply_needed=toggle_reply_needed,
    toggle_platform_wide=toggle_platform_wide,
    render_tickets_board=render_tickets_board,
    render_report=render_report,
    se_timezone=settings.se_timezone,
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

    if linear_enabled:
        # Opens the aiohttp session + auto-resolves project / state / actor ids.
        await linear_gateway.start()
        # Wire the resolved actor id into the inbound self-echo filter unless it
        # was pinned via config. (With a personal token this is your own user;
        # use a dedicated Linear bot account in production so human edits sync.)
        if linear_inbound.actor_id is None:
            linear_inbound.actor_id = linear_gateway.actor_id
        logger.info("Linear gateway started (actor_id=%s)", linear_inbound.actor_id)

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

    # Open-tickets digest — 30-min loop checks for the 10:00 and 17:00 SE-local
    # windows; DMs the SE one roll-up of tickets needing action (New + In
    # progress), with counts by tier and a Reply-needed marker. This is the sole
    # SE ticket notification — it replaces the per-transition SLA escalation DMs
    # and folds in the old weekly + reply-needed digests.
    open_digest_task = asyncio.create_task(
        open_tickets_digest_job.run_loop(interval_seconds=1800),
        name="open-tickets-digest",
    )
    open_digest_task.add_done_callback(_log_task_result)
    background_tasks.append(open_digest_task)

    # Linear reconcile — 10-min no-desync backstop: re-mirrors any ticket whose
    # outbound create was dropped, and pulls any dev-lane Linear state change a
    # missed webhook left unreflected. Only runs when Linear is configured.
    if linear_enabled:
        reconcile_task = asyncio.create_task(
            reconcile_linear.run_loop(),
            name="linear-reconcile",
        )
        reconcile_task.add_done_callback(_log_task_result)
        background_tasks.append(reconcile_task)

    yield

    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await slack_integration.stop()
    if linear_enabled:
        await linear_gateway.stop()
    await engine.dispose()
    logger.info("Shutdown complete")


# --- FastAPI App ---
api = FastAPI(lifespan=lifespan)
slack_integration.register_routes(api)
in_app_bug_webhook.register_routes(api)
linear_webhook.register_routes(api)


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}
