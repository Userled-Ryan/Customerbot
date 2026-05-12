import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slack_sdk.web.async_client import AsyncWebClient

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
from customerbot.integration.slack.gateway import SlackGateway
from customerbot.integration.slack.handler import SlackIntegration

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

# --- Slack Gateway ---
slack_client = AsyncWebClient(token=settings.slack.bot_token)
gateway = SlackGateway(client=slack_client, workspace_url=settings.slack.workspace_url)

# --- Use Cases ---
handle_incoming_message = HandleIncomingMessage(
    repo=conversation_repo,
    keywords=keyword_repo,
    messenger=gateway,
    ryan_user_id=settings.ryan_user_id,
)
add_manual_ticket = AddManualTicket(
    repo=conversation_repo,
    messenger=gateway,
)
build_summary = BuildSummary(
    repo=conversation_repo,
    messenger=gateway,
    user_settings_repo=user_settings_repo,
    ryan_user_id=settings.ryan_user_id,
    reminder_hours=settings.reminder_hours,
)
send_reminders = SendReminders(
    repo=conversation_repo,
    messenger=gateway,
    user_settings_repo=user_settings_repo,
    ryan_user_id=settings.ryan_user_id,
    reminder_hours=settings.reminder_hours,
)
send_daily_digest = SendDailyDigest(
    repo=conversation_repo,
    messenger=gateway,
    user_settings_repo=user_settings_repo,
    ryan_user_id=settings.ryan_user_id,
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
    ryan_user_id=settings.ryan_user_id,
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

    reminder_task = asyncio.create_task(
        send_reminders.run_loop(interval_seconds=3600),
        name="reminder-loop",
    )
    reminder_task.add_done_callback(_log_task_result)

    digest_task = asyncio.create_task(
        send_daily_digest.run_loop(interval_seconds=60),
        name="digest-loop",
    )
    digest_task.add_done_callback(_log_task_result)

    yield

    reminder_task.cancel()
    digest_task.cancel()
    for task in (reminder_task, digest_task):
        try:
            await task
        except asyncio.CancelledError:
            pass

    await slack_integration.stop()
    await engine.dispose()
    logger.info("Shutdown complete")


# --- FastAPI App ---
api = FastAPI(lifespan=lifespan)
slack_integration.register_routes(api)


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}
