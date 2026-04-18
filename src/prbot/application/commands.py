"""Slash-command definitions for bot configuration.

Each command is a self-contained unit: it knows its name, argument
validation, and how to format results.  The dispatcher is a thin
registry that routes by name and generates the help text.

Commands are integration-agnostic — they accept plain strings and
return plain strings.  Integration layers (Slack, Discord, …) only
need to parse the raw input into (subcommand, args, scope_keys) and
display the result.
"""

from __future__ import annotations

import logging
from typing import Protocol

from prbot.application.manage_scope_config import ManageUserExclusions
from prbot.domain.ports import EmojiConfigResolverPort

logger = logging.getLogger(__name__)

# Valid scope level names and their index into the scope_keys list.
# scope_keys are ordered most-specific first: [channel, workspace, integration]
_SCOPE_LEVELS: dict[str, int] = {
    "channel": 0,
    "workspace": 1,
}
_SCOPE_LABEL_BY_INDEX: tuple[str, ...] = ("channel", "workspace", "global")


def _resolve_scope(scope_keys: list[str], scope_arg: str | None) -> str | None:
    """Pick a scope key from the hierarchy based on an optional level argument.

    Returns None if the scope_arg is invalid.
    """
    if scope_arg is None:
        return scope_keys[0] if scope_keys else None
    idx = _SCOPE_LEVELS.get(scope_arg.lower())
    if idx is None:
        return None
    if idx >= len(scope_keys):
        return scope_keys[-1] if scope_keys else None
    return scope_keys[idx]


def _resolve_scopes(scope_keys: list[str], scope_arg: str | None) -> list[str] | None:
    """Pick which scopes to query based on an optional level argument.

    With no arg, returns the full hierarchy so inherited entries surface.
    With a valid level, returns a single-element list for that level.
    Returns None if the scope_arg is invalid.
    """
    if scope_arg is None:
        return list(scope_keys)
    single = _resolve_scope(scope_keys, scope_arg)
    return None if single is None else [single]


def _label_for_scope(scope_keys: list[str], scope_key: str) -> str:
    try:
        idx = scope_keys.index(scope_key)
    except ValueError:
        return "scope"
    if idx < len(_SCOPE_LABEL_BY_INDEX):
        return _SCOPE_LABEL_BY_INDEX[idx]
    return "scope"


class Command(Protocol):
    """A single slash-command subcommand."""

    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> tuple[str, ...]: ...

    @property
    def usage(self) -> str: ...

    async def execute(self, args: list[str], scope_keys: list[str]) -> str: ...


# --- Concrete commands ---


class ExcludeCommand:
    name = "exclude"
    aliases = ()
    usage = "`exclude <username> [channel|workspace]` — exclude a user from PR status updates"

    def __init__(self, manage_exclusions: ManageUserExclusions) -> None:
        self._manage_exclusions = manage_exclusions

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        if not args or len(args) > 2:
            return f"Usage: `/prbot {self.usage}`"
        username = args[0]
        scope_key = _resolve_scope(scope_keys, args[1] if len(args) == 2 else None)
        if scope_key is None:
            return f"Unknown scope `{args[1]}`. Use `channel` or `workspace`."
        result = await self._manage_exclusions.exclude_user(scope_key, username)
        if result.was_already:
            return f"`{result.username}` is already excluded in `{scope_key}`."
        return f"Excluded `{result.username}` from PR status updates in `{scope_key}`."


class IncludeCommand:
    name = "include"
    aliases = ()
    usage = "`include <username> [channel|workspace]` — re-include a previously excluded user"

    def __init__(self, manage_exclusions: ManageUserExclusions) -> None:
        self._manage_exclusions = manage_exclusions

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        if not args or len(args) > 2:
            return f"Usage: `/prbot {self.usage}`"
        username = args[0]
        scope_key = _resolve_scope(scope_keys, args[1] if len(args) == 2 else None)
        if scope_key is None:
            return f"Unknown scope `{args[1]}`. Use `channel` or `workspace`."
        result = await self._manage_exclusions.include_user(scope_key, username)
        if result.was_already:
            return f"`{result.username}` is not excluded in `{scope_key}`."
        return f"Re-included `{result.username}` in PR status updates in `{scope_key}`."


class ListExclusionsCommand:
    name = "list-exclusions"
    aliases = ("exclusions",)
    usage = "`list-exclusions [channel|workspace]` — show excluded users"

    def __init__(self, manage_exclusions: ManageUserExclusions) -> None:
        self._manage_exclusions = manage_exclusions

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        target_scopes = _resolve_scopes(scope_keys, args[0] if args else None)
        if target_scopes is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        grouped = await self._manage_exclusions.list_excluded_users(target_scopes)
        if not grouped:
            if len(target_scopes) == 1:
                return f"No users are excluded in `{target_scopes[0]}`."
            return "No users are excluded."
        lines = ["*Excluded users:*"]
        for scope_key in target_scopes:
            users = grouped.get(scope_key)
            if not users:
                continue
            label = _label_for_scope(scope_keys, scope_key).capitalize()
            formatted = ", ".join(f"`{u}`" for u in users)
            lines.append(f"• *{label}* (`{scope_key}`): {formatted}")
        return "\n".join(lines)


class ShowConfigCommand:
    name = "config"
    aliases = ()
    usage = "`config [channel|workspace]` — show configuration"

    def __init__(
        self,
        manage_exclusions: ManageUserExclusions,
        emoji_resolver: EmojiConfigResolverPort,
    ) -> None:
        self._manage_exclusions = manage_exclusions
        self._emoji_resolver = emoji_resolver

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        scope_key = _resolve_scope(scope_keys, args[0] if args else None)
        if scope_key is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        grouped = await self._manage_exclusions.list_excluded_users([scope_key])
        emoji = await self._emoji_resolver.resolve([scope_key])
        excluded = ", ".join(f"`{u}`" for u in grouped.get(scope_key, [])) or "none"
        lines = [
            f"*Scope:* `{scope_key}`",
            f"*Excluded users:* {excluded}",
            "*Emoji config:*",
            f"  merged: `{emoji.merged}`",
            f"  closed: `{emoji.closed}`",
            f"  approved: `{emoji.approved}`",
            f"  changes requested: `{emoji.changes_requested}`",
            f"  commented: `{emoji.commented}`",
        ]
        return "\n".join(lines)


# --- Dispatcher ---


class CommandDispatcher:
    """Routes subcommand strings to Command instances."""

    def __init__(self, commands: list[Command]) -> None:
        self._commands: dict[str, Command] = {}
        self._ordered: list[Command] = commands
        for cmd in commands:
            self._commands[cmd.name] = cmd
            for alias in cmd.aliases:
                self._commands[alias] = cmd

    async def dispatch(self, subcommand: str, args: list[str], scope_keys: list[str]) -> str:
        cmd = self._commands.get(subcommand)
        if cmd is None:
            return self._help_text()
        return await cmd.execute(args, scope_keys)

    def _help_text(self) -> str:
        lines = ["Usage: `/prbot <command>`"]
        for cmd in self._ordered:
            lines.append(f"• {cmd.usage}")
        return "\n".join(lines)


def build_default_dispatcher(
    manage_exclusions: ManageUserExclusions,
    emoji_resolver: EmojiConfigResolverPort,
) -> CommandDispatcher:
    """Build the standard command dispatcher with all built-in commands."""
    return CommandDispatcher(
        [
            ExcludeCommand(manage_exclusions),
            IncludeCommand(manage_exclusions),
            ListExclusionsCommand(manage_exclusions),
            ShowConfigCommand(manage_exclusions, emoji_resolver),
        ]
    )
