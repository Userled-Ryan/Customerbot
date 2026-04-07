import logging
from collections.abc import AsyncIterator

import discord

from prbot.application.backfill_missed_messages import HistoryItem
from prbot.domain.value_objects import MessageRef

logger = logging.getLogger(__name__)

INTEGRATION_ID = "discord"


def encode_ref(channel_id: str, message_id: str) -> MessageRef:
    """Encode a Discord channel and message ID into a MessageRef."""
    return MessageRef(integration_id=INTEGRATION_ID, ref=f"{channel_id}:{message_id}")


def decode_ref(message_ref: MessageRef) -> tuple[int, int]:
    """Decode a Discord MessageRef into (channel_id, message_id) as ints."""
    channel_str, message_str = message_ref.ref.split(":", 1)
    return int(channel_str), int(message_str)


class DiscordGateway:
    """Concrete adapter: manages Discord emoji reactions via the Discord API."""

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    def _resolve_emoji(self, emoji: str) -> str | discord.Emoji:
        """Resolve an emoji reference to a Discord-compatible value.

        If the string is a Unicode emoji (non-ASCII), return it as-is.
        Otherwise, look up a custom guild emoji by name.
        """
        if not emoji.isascii():
            return emoji

        for guild_emoji in self._client.emojis:
            if guild_emoji.name == emoji:
                return guild_emoji

        # Fall back to the raw string — Discord will reject it if it's
        # neither valid Unicode nor a known custom emoji.
        return emoji

    async def add_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        channel_id, message_id = decode_ref(message_ref)
        channel = self._client.get_channel(channel_id)
        if channel is None:
            channel = await self._client.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            logger.warning("Channel %d is not messageable, skipping reaction", channel_id)
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.add_reaction(self._resolve_emoji(emoji))
        except discord.NotFound:
            logger.warning("Message %d not found in channel %d", message_id, channel_id)
        except discord.HTTPException as exc:
            if exc.code == 30010:
                logger.debug("Max reactions reached for message %d", message_id)
            else:
                raise

    def list_bot_guilds(self) -> list[discord.Guild]:
        """Return all guilds the bot is currently in."""
        return list(self._client.guilds)

    def list_text_channels(self, guild: discord.Guild) -> list[discord.TextChannel]:
        """Return all text channels in a guild the bot can read."""
        return [
            ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history
        ]

    async def fetch_channel_history(
        self,
        channel_id: str,
        team_id: str,
        oldest: str | None = None,
    ) -> AsyncIterator[HistoryItem]:
        """Fetch messages from a channel, optionally since a given message ID."""
        channel = self._client.get_channel(int(channel_id))
        if channel is None or not isinstance(channel, discord.TextChannel):
            return

        after = discord.Object(id=int(oldest)) if oldest else None

        async for message in channel.history(limit=None, after=after, oldest_first=True):
            if message.author.bot:
                continue
            yield HistoryItem(
                text=message.content,
                ts=str(message.id),
                channel_id=channel_id,
                team_id=team_id,
            )
