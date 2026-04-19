from unittest.mock import MagicMock

import discord
from discord import app_commands

from prbot.application.commands import CommandDispatcher
from prbot.integration.discord.commands import register_commands


def _build_tree() -> app_commands.CommandTree:
    # CommandTree only needs a client stub with the attributes it pokes at;
    # we don't actually connect to Discord in the test.
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    return app_commands.CommandTree(client)


class TestRegisterCommands:
    def test_registers_full_command_tree(self) -> None:
        tree = _build_tree()
        dispatcher = MagicMock(spec=CommandDispatcher)

        register_commands(tree, dispatcher, lambda **kwargs: ["discord"])

        prbot = tree.get_command("prbot")
        assert isinstance(prbot, app_commands.Group)

        subgroup_names = {
            c.name for c in prbot.walk_commands() if isinstance(c, app_commands.Group)
        }
        assert subgroup_names == {"exclusions", "self-reviews", "emoji"}

        leaf_names: set[str] = set()
        for c in prbot.walk_commands():
            if isinstance(c, app_commands.Group) or c.parent is None:
                continue
            leaf_names.add(f"{c.parent.name}:{c.name}")
        assert leaf_names == {
            "exclusions:add",
            "exclusions:remove",
            "exclusions:list",
            "self-reviews:mute",
            "self-reviews:unmute",
            "self-reviews:status",
            "emoji:status",
        }
