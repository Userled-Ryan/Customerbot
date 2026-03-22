import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from starlette.responses import Response

from prbot.application.handle_github_webhook import HandleGitHubWebhook
from prbot.application.handle_slack_message import HandleSlackMessage
from prbot.config import Settings
from prbot.infrastructure.github_gateway import GitHubGateway
from prbot.infrastructure.github_webhook_models import (
    PullRequestEvent,
    PullRequestReviewEvent,
)
from prbot.infrastructure.slack_gateway import SlackGateway
from prbot.infrastructure.sqlite_repository import SQLitePRRepository
from prbot.infrastructure.webhook_verification import verify_github_signature

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()  # type: ignore[call-arg]

# --- Infrastructure ---
github_gateway = GitHubGateway(token=settings.github_token)
pr_repository = SQLitePRRepository(db_path=settings.database_path)

# --- Slack Bolt App ---
bolt_app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)
slack_client = AsyncWebClient(token=settings.slack_bot_token)
slack_gateway = SlackGateway(client=slack_client)

# --- Use Cases ---
handle_slack_message = HandleSlackMessage(
    github_client=github_gateway,
    slack_reactions=slack_gateway,
    pr_repository=pr_repository,
)
handle_github_webhook = HandleGitHubWebhook(
    github_client=github_gateway,
    slack_reactions=slack_gateway,
    pr_repository=pr_repository,
)

# --- Slack Event Handlers ---
_PR_URL_REGEX = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/\d+")


@bolt_app.event("message")
async def on_message(event: dict[str, object]) -> None:
    text = str(event.get("text", ""))
    if not _PR_URL_REGEX.search(text):
        return
    channel = str(event["channel"])
    ts = str(event["ts"])
    await handle_slack_message.execute(channel_id=channel, message_ts=ts, text=text)


# --- FastAPI Lifespan ---
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    await pr_repository.initialize()
    logger.info("Database initialized")
    yield
    await pr_repository.close()
    await github_gateway.close()
    logger.info("Shutdown complete")


# --- FastAPI App ---
api = FastAPI(lifespan=lifespan)
slack_handler = AsyncSlackRequestHandler(bolt_app)


@api.post("/slack/events")
async def slack_events(req: Request) -> Response:
    """Slack events endpoint — handled by slack-bolt via ASGI adapter."""
    return await slack_handler.handle(req)


@api.post("/github/webhooks")
async def github_webhooks(req: Request) -> dict[str, bool]:
    """GitHub webhook endpoint with HMAC-SHA256 verification."""
    body = await req.body()
    signature = req.headers.get("X-Hub-Signature-256")

    if not verify_github_signature(body, settings.github_webhook_secret, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    event_type = req.headers.get("X-GitHub-Event")

    if event_type not in ("pull_request", "pull_request_review"):
        logger.warning("Ignoring GitHub event: %s", event_type)
        return {"ok": True}

    logger.info("Received GitHub event: %s", event_type)

    if not body:
        logger.warning("Empty body for %s event, skipping", event_type)
        return {"ok": True}

    payload = await req.json()

    if event_type == "pull_request":
        event = PullRequestEvent.model_validate(payload)
        logger.info(
            "PR event: %s %s#%d",
            event.action,
            event.repository.full_name,
            event.pull_request.number,
        )
        if event.action in ("opened", "closed", "reopened", "synchronize"):
            owner, repo = event.repository.full_name.split("/")
            await handle_github_webhook.execute(
                owner=owner, repo=repo, number=event.pull_request.number
            )

    elif event_type == "pull_request_review":
        review_event = PullRequestReviewEvent.model_validate(payload)
        logger.info(
            "PR review event: %s %s#%d",
            review_event.action,
            review_event.repository.full_name,
            review_event.pull_request.number,
        )
        if review_event.action in ("submitted", "dismissed"):
            owner, repo = review_event.repository.full_name.split("/")
            await handle_github_webhook.execute(
                owner=owner, repo=repo, number=review_event.pull_request.number
            )

    return {"ok": True}


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}
