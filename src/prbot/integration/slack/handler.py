import logging
import re

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from starlette.responses import Response

from prbot.application.handle_incoming_message import HandleIncomingMessage
from prbot.config import SlackConfig
from prbot.domain.ports import ReactionPort
from prbot.integration.slack.gateway import INTEGRATION_ID, SlackGateway, encode_ref

logger = logging.getLogger(__name__)

_PR_URL_REGEX = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/\d+")


class SlackIntegration:
    """Slack integration: listens for messages via Slack Events API and adds emoji reactions."""

    def __init__(
        self,
        config: SlackConfig,
        handle_incoming_message: HandleIncomingMessage,
    ) -> None:
        self._config = config
        self._handle_incoming_message = handle_incoming_message
        self._bolt_app = AsyncApp(
            token=config.bot_token,
            signing_secret=config.signing_secret,
        )
        self._client = AsyncWebClient(token=config.bot_token)
        self._gateway = SlackGateway(client=self._client)
        self._setup_events()

    def _setup_events(self) -> None:
        @self._bolt_app.event("message")
        async def on_message(event: dict[str, object]) -> None:
            text = str(event.get("text", ""))
            channel = str(event.get("channel", ""))
            ts = str(event.get("ts", ""))
            team = str(event.get("team", ""))
            logger.info("Slack message in %s: %s", channel, text[:100])

            if not _PR_URL_REGEX.search(text):
                return

            message_ref = encode_ref(channel, ts)
            scope_keys = _build_scope_keys(team=team, channel=channel)
            logger.info("Found PR URL in message, processing %s", message_ref.ref)
            await self._handle_incoming_message.execute(
                message_ref=message_ref, text=text, scope_keys=scope_keys
            )

    @property
    def integration_id(self) -> str:
        return INTEGRATION_ID

    def reaction_port(self) -> ReactionPort:
        return self._gateway

    def register_routes(self, app: FastAPI) -> None:
        handler = AsyncSlackRequestHandler(self._bolt_app)

        @app.post("/slack/events")
        async def slack_events(req: Request) -> Response:
            """Slack events endpoint — handled by slack-bolt via ASGI adapter."""
            return await handler.handle(req)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _build_scope_keys(team: str, channel: str) -> list[str]:
    """Build scope keys for Slack, most-specific first.

    Scope key format: <integration_id>/<workspace_id>[/<channel_id>]
    """
    iid = INTEGRATION_ID
    keys: list[str] = []
    if team and channel:
        keys.append(f"{iid}/{team}/{channel}")
    if team:
        keys.append(f"{iid}/{team}")
    keys.append(iid)
    return keys
