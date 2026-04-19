"""Discord native slash commands for /prbot config.

Maps Discord's structured `discord.app_commands` onto the integration-agnostic
`CommandDispatcher` defined in `prbot.application.commands`. Every leaf
command here translates its typed parameters into the positional
`(subcommand, args, scope_keys)` tuple the dispatcher expects.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

import discord
from discord import app_commands

from prbot.application.commands import CommandDispatcher

logger = logging.getLogger(__name__)

# Discord choice parameter — limited to 25 options per param; we only need 2.
ScopeChoice = Literal["channel", "workspace"]

ScopeKeysFn = Callable[..., list[str]]


def register_commands(
    tree: app_commands.CommandTree,
    dispatcher: CommandDispatcher,
    build_scope_keys: ScopeKeysFn,
) -> None:
    """Register /prbot slash commands on the given tree."""

    prbot = app_commands.Group(
        name="prbot",
        description="PR bot configuration",
    )
    exclusions = app_commands.Group(
        name="exclusions",
        description="Skip PR status updates from specific GitHub users",
        parent=prbot,
    )
    self_reviews = app_commands.Group(
        name="self-reviews",
        description="Suppress reactions on the PR author's own review comments",
        parent=prbot,
    )
    emoji = app_commands.Group(
        name="emoji",
        description="View the PR-status emoji mapping",
        parent=prbot,
    )

    async def run(interaction: discord.Interaction, subcommand: str, args: list[str]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        scope_keys = build_scope_keys(
            guild=str(interaction.guild_id or ""),
            channel=str(interaction.channel_id or ""),
        )
        try:
            response = await dispatcher.dispatch(subcommand, args, scope_keys)
        except Exception:
            logger.exception("Error handling /prbot %s %s", subcommand, args)
            response = "Something went wrong processing that command."
        await interaction.followup.send(content=response, ephemeral=True)

    @exclusions.command(name="add", description="Exclude a GitHub user from PR status updates")
    @app_commands.describe(user="GitHub username", scope="Scope to apply the exclusion at")
    async def exclusions_add(
        interaction: discord.Interaction,
        user: str,
        scope: ScopeChoice = "channel",
    ) -> None:
        await run(interaction, "exclusions", ["add", user, scope])

    @exclusions.command(name="remove", description="Re-include a previously excluded GitHub user")
    @app_commands.describe(user="GitHub username", scope="Scope to remove the exclusion at")
    async def exclusions_remove(
        interaction: discord.Interaction,
        user: str,
        scope: ScopeChoice = "channel",
    ) -> None:
        await run(interaction, "exclusions", ["remove", user, scope])

    @exclusions.command(name="list", description="List excluded GitHub users")
    @app_commands.describe(scope="Scope to list exclusions for (default: show inherited)")
    async def exclusions_list(
        interaction: discord.Interaction,
        scope: ScopeChoice | None = None,
    ) -> None:
        args = ["list"]
        if scope is not None:
            args.append(scope)
        await run(interaction, "exclusions", args)

    @self_reviews.command(name="mute", description="Mute reactions on the author's self-reviews")
    @app_commands.describe(scope="Scope to apply the mute at")
    async def self_reviews_mute(
        interaction: discord.Interaction,
        scope: ScopeChoice = "channel",
    ) -> None:
        await run(interaction, "self-reviews", ["mute", scope])

    @self_reviews.command(name="unmute", description="Unmute reactions on self-reviews")
    @app_commands.describe(scope="Scope to remove the mute at")
    async def self_reviews_unmute(
        interaction: discord.Interaction,
        scope: ScopeChoice = "channel",
    ) -> None:
        await run(interaction, "self-reviews", ["unmute", scope])

    @self_reviews.command(name="status", description="Show whether self-reviews are muted")
    @app_commands.describe(scope="Scope to query (default: show inherited)")
    async def self_reviews_status(
        interaction: discord.Interaction,
        scope: ScopeChoice | None = None,
    ) -> None:
        args = ["status"]
        if scope is not None:
            args.append(scope)
        await run(interaction, "self-reviews", args)

    @emoji.command(name="status", description="Show the effective emoji config")
    @app_commands.describe(scope="Scope to query (default: show inherited)")
    async def emoji_status(
        interaction: discord.Interaction,
        scope: ScopeChoice | None = None,
    ) -> None:
        args = ["status"]
        if scope is not None:
            args.append(scope)
        await run(interaction, "emoji", args)

    tree.add_command(prbot)
