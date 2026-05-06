from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient
from starlette.responses import Response

from customerbot.application.tracking.build_summary import BuildSummary
from customerbot.application.tracking.handle_incoming_message import HandleIncomingMessage
from customerbot.config import SlackConfig
from customerbot.domain.tracking.ports import ConversationRepositoryPort
from customerbot.domain.tracking.value_objects import ConversationStatus
from customerbot.integration.slack.gateway import INTEGRATION_ID, SlackGateway

logger = logging.getLogger(__name__)


class SlackIntegration:
    """Slack integration: monitors channels, tracks conversations, and responds to commands."""

    def __init__(
        self,
        config: SlackConfig,
        handle_incoming_message: HandleIncomingMessage,
        build_summary: BuildSummary,
        conversation_repo: ConversationRepositoryPort,
        ryan_user_id: str,
    ) -> None:
        self._config = config
        self._handle_incoming_message = handle_incoming_message
        self._build_summary = build_summary
        self._conversation_repo = conversation_repo
        self._ryan_user_id = ryan_user_id
        self._bolt_app = AsyncApp(
            token=config.bot_token,
            signing_secret=config.signing_secret,
        )
        self._client = AsyncWebClient(token=config.bot_token)
        self._gateway = SlackGateway(
            client=self._client,
            workspace_url=config.workspace_url,
        )
        self._setup_events()
        self._setup_commands()

    @property
    def integration_id(self) -> str:
        return INTEGRATION_ID

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

            # Skip DMs — bot messages to/from individuals are not customer tickets
            if channel.startswith("D"):
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
        @self._bolt_app.command("/customerbot")
        async def on_command(ack: AsyncAck, command: dict[str, object]) -> None:
            await ack()
            text = str(command.get("text", "")).strip().lower()
            channel = str(command.get("channel_id", ""))
            thread_ts = str(command.get("thread_ts", "") or "")
            user_id = str(command.get("user_id", ""))

            parts = text.split()
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
                    await self._gateway.send_message(
                        channel_id=channel,
                        text=f"✅ Closed all {len(open_convs)} open ticket(s).",
                    )
                    return

                # close 1 2 3 ...
                if args and all(a.isdigit() for a in args):
                    closed, already_closed, not_found = [], [], []
                    for arg in args:
                        ticket_id = int(arg)
                        conv = await self._conversation_repo.find_by_id(ticket_id)
                        if conv is None:
                            not_found.append(ticket_id)
                        elif conv.status == ConversationStatus.CLOSED:
                            already_closed.append(ticket_id)
                        else:
                            await self._conversation_repo.update_status(
                                conv.channel_id, conv.thread_ts, ConversationStatus.CLOSED
                            )
                            label = conv.channel_name or conv.channel_id
                            closed.append((ticket_id, label))

                    lines = []
                    if closed:
                        lines.append("✅ Closed: " + ", ".join(f"`#{tid}` (#{lbl})" for tid, lbl in closed))
                    if already_closed:
                        lines.append("ℹ️ Already closed: " + ", ".join(f"`#{tid}`" for tid in already_closed))
                    if not_found:
                        lines.append("⚠️ Not found: " + ", ".join(f"`#{tid}`" for tid in not_found))
                    await self._gateway.send_message(channel_id=channel, text="\n".join(lines))
                    return

                # Fallback: close the current thread (when run inside a tracked thread)
                target_ts = thread_ts or None
                if not target_ts:
                    await self._gateway.send_message(
                        channel_id=channel,
                        text="⚠️ Usage: `/customerbot close <id> [id ...]` or `/customerbot close all` — find IDs via `/customerbot summary`.",
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
                # Ephemeral so customers in the thread don't see a bot confirmation
                await self._gateway.send_ephemeral(
                    channel_id=channel,
                    user_id=user_id,
                    text=f"✅ Closed ticket `#{conv.id}`.",
                )
                return

            help_text = (
                "*CustomerBot commands*\n"
                "• `/customerbot` or `/customerbot summary` — show open tickets with IDs\n"
                "• `/customerbot close <id>` — close a ticket by ID (works from anywhere)\n"
                "• `/customerbot close <id> <id> ...` — close multiple tickets at once\n"
                "• `/customerbot close all` — close all open tickets\n"
                "• `/customerbot close` — close the current thread's ticket (when used inside a thread)"
            )
            await self._gateway.send_message(channel_id=channel, text=help_text)

    def register_routes(self, app: FastAPI) -> None:
        handler = AsyncSlackRequestHandler(self._bolt_app)

        @app.post("/slack/events")
        async def slack_events(req: Request) -> Response:
            return await handler.handle(req)

    async def start(self) -> None:
        logger.info("CustomerBot Slack integration started")

    async def stop(self) -> None:
        pass
