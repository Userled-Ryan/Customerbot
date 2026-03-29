import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from prbot.application.handle_github_webhook import HandleGitHubWebhook
from prbot.application.handle_incoming_message import HandleIncomingMessage
from prbot.config import Settings
from prbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
    run_migrations,
)
from prbot.data.repository import SQLitePRRepository
from prbot.data.scope_config import ScopeConfigEmojiResolver
from prbot.infrastructure.github_gateway import GitHubGateway
from prbot.infrastructure.github_webhook_models import (
    PullRequestEvent,
    PullRequestReviewEvent,
)
from prbot.infrastructure.webhook_verification import verify_github_signature
from prbot.integration import IntegrationRegistry
from prbot.integration.slack.handler import SlackIntegration

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()  # type: ignore[call-arg]

# --- Infrastructure ---
github_gateway = GitHubGateway(
    app_id=settings.github_app_id,
    private_key=settings.github_private_key,
)
database_url = database_url_from_path(settings.database_path)
engine = make_engine(database_url)
session_factory = make_session_factory(engine)
pr_repository = SQLitePRRepository(session_factory=session_factory)
emoji_resolver = ScopeConfigEmojiResolver(
    session_factory=session_factory,
    default=settings.emoji,
)

# --- Integration Registry ---
registry = IntegrationRegistry()

# --- Use Cases ---
handle_incoming_message = HandleIncomingMessage(
    sources=[github_gateway],
    reactions=registry,
    pr_repository=pr_repository,
    emoji_resolver=emoji_resolver,
)
handle_github_webhook = HandleGitHubWebhook(
    source=github_gateway,
    reactions=registry,
    pr_repository=pr_repository,
    emoji_resolver=emoji_resolver,
)

# --- Register Integrations ---
if settings.slack is not None:
    slack_integration = SlackIntegration(
        config=settings.slack,
        handle_incoming_message=handle_incoming_message,
    )
    registry.register(slack_integration)


# --- FastAPI Lifespan ---
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    run_migrations(database_url)
    logger.info("Database migrations applied")
    await registry.start_all()
    yield
    await registry.stop_all()
    await engine.dispose()
    await github_gateway.close()
    logger.info("Shutdown complete")


# --- FastAPI App ---
api = FastAPI(lifespan=lifespan)
registry.register_all_routes(api)


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
