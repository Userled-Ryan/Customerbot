import asyncio
import logging
import re

import discord

from prbot.application.backfill_missed_messages import (
    BackfillMissedMessages,
    ChannelDescriptor,
)
from prbot.application.handle_incoming_message import HandleIncomingMessage
from prbot.config import DiscordConfig
from prbot.domain.ports import ChannelCursorPort, ReactionPort
from prbot.integration.discord.gateway import (
    INTEGRATION_ID,
    DiscordGateway,
    encode_ref,
)

logger = logging.getLogger(__name__)

_PR_URL_REGEX = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/\d+")


class DiscordIntegration:
    """Discord integration: listens for messages via Discord gateway and adds emoji reactions."""

    def __init__(
        self,
        config: DiscordConfig,
        handle_incoming_message: HandleIncomingMessage,
        cursor_repo: ChannelCursorPort,
        backfill: BackfillMissedMessages,
    ) -> None:
        self._config = config
        self._handle_incoming_message = handle_incoming_message
        self._cursor_repo = cursor_repo
        self._backfill = backfill

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        self._client = discord.Client(intents=intents)
        self._gateway = DiscordGateway(client=self._client)
        self._bot_task: asyncio.Task[None] | None = None
        self._ready_event = asyncio.Event()
        self._setup_events()

    def _setup_events(self) -> None:
        @self._client.event
        async def on_ready() -> None:
            logger.info("Discord bot connected as %s", self._client.user)
            self._ready_event.set()

        @self._client.event
        async def on_message(message: discord.Message) -> None:
            if message.author.bot or message.guild is None:
                return

            text = message.content
            channel_id = str(message.channel.id)
            message_id = str(message.id)
            guild_id = str(message.guild.id)
            logger.info("Discord message in %s: %s", channel_id, text[:100])

            # Always advance the cursor, even for non-PR messages
            await self._cursor_repo.upsert_cursor(INTEGRATION_ID, channel_id, message_id)

            if not _PR_URL_REGEX.search(text):
                return

            message_ref = encode_ref(channel_id, message_id)
            scope_keys = build_scope_keys(guild=guild_id, channel=channel_id)
            logger.info("Found PR URL in message, processing %s", message_ref.ref)
            await self._handle_incoming_message.execute(
                message_ref=message_ref, text=text, scope_keys=scope_keys
            )

    @property
    def integration_id(self) -> str:
        return INTEGRATION_ID

    def reaction_port(self) -> ReactionPort:
        return self._gateway

    def register_routes(self, app: object) -> None:
        pass

    async def start(self) -> None:
        self._bot_task = asyncio.create_task(
            self._client.start(self._config.bot_token),
            name="discord-bot",
        )
        await self._ready_event.wait()

        guilds = self._gateway.list_bot_guilds()
        descriptors: list[ChannelDescriptor] = []
        for guild in guilds:
            for ch in self._gateway.list_text_channels(guild):
                descriptors.append(ChannelDescriptor(channel_id=str(ch.id), team_id=str(guild.id)))

        await self._backfill.execute(
            channels=descriptors,
            fetch_history=lambda ch, oldest: self._gateway.fetch_channel_history(
                channel_id=ch.channel_id, team_id=ch.team_id, oldest=oldest
            ),
        )

    async def stop(self) -> None:
        await self._client.close()
        if self._bot_task and not self._bot_task.done():
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                logger.info("Discord bot task cancelled")


def build_scope_keys(guild: str, channel: str) -> list[str]:
    """Build scope keys for Discord, most-specific first.

    Scope key format: <integration_id>/<guild_id>[/<channel_id>]
    """
    iid = INTEGRATION_ID
    keys: list[str] = []
    if guild and channel:
        keys.append(f"{iid}/{guild}/{channel}")
    if guild:
        keys.append(f"{iid}/{guild}")
    keys.append(iid)
    return keys
