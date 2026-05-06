from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient
from starlette.responses import Response

from customerbot.application.tracking.add_manual_ticket import AddManualTicket
from customerbot.application.tracking.build_summary import BuildSummary
from customerbot.application.tracking.handle_incoming_message import HandleIncomingMessage
from customerbot.config import SlackConfig
from customerbot.domain.tracking.ports import (
    ConversationRepositoryPort,
    KeywordRepositoryPort,
)
from customerbot.domain.tracking.value_objects import ConversationStatus
from customerbot.integration.slack.gateway import INTEGRATION_ID, SlackGateway

logger = logging.getLogger(__name__)


def _split_keyword_and_category(text: str) -> tuple[str, str | None]:
    """Split `<word> as <category>` on the last ' as '; either side may contain spaces."""
    sep = " as "
    idx = text.rfind(sep)
    if idx == -1:
        return text.strip(), None
    word = text[:idx].strip()
    category = text[idx + len(sep):].strip()
    return word, category or None


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
        ryan_user_id: str,
    ) -> None:
        self._config = config
        self._handle_incoming_message = handle_incoming_message
        self._build_summary = build_summary
        self._add_manual_ticket = add_manual_ticket
        self._conversation_repo = conversation_repo
        self._keyword_repo = keyword_repo
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
                        lines.append("✅ Closed: " + ", ".join(f"`#{tid}` (#{lbl})" for tid, lbl in closed))
                    if not_found:
                        lines.append("⚠️ Not found: " + ", ".join(f"`#{tid}`" for tid in not_found))
                    await self._gateway.send_message(channel_id=channel, text="\n".join(lines))
                    return

                # Fallback: close the current thread (when run inside a tracked thread)
                target_ts = thread_ts or None
                if not target_ts:
                    await self._gateway.send_message(
                        channel_id=channel,
                        text="⚠️ Usage: `/csbot close <id> [id ...]` or `/csbot close all` — find IDs via `/csbot summary`.",
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
                        msg = "ℹ️ No keywords configured — no tickets will be created until you add one."
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

            help_text = (
                "*CustomerBot commands*\n"
                "• `/csbot` or `/csbot summary` — show open tickets with IDs\n"
                "• `/csbot close <id>` — close a ticket by ID (works from anywhere)\n"
                "• `/csbot close <id> <id> ...` — close multiple tickets at once\n"
                "• `/csbot close all` — close all open tickets\n"
                "• `/csbot close` — close the current thread's ticket (when used inside a thread)\n"
                "• `/csbot keyword add <word> [as <category>]` — track a keyword and optionally tag matching tickets with a category (e.g. `look as bug`)\n"
                "• `/csbot keyword remove <word>` — stop tracking a keyword\n"
                "• `/csbot keyword list` — list all tracked keywords\n"
                "_DM me a Slack thread link to manually open a ticket for that thread._"
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
