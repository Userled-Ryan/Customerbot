"""Slash-command definitions for bot configuration.

Shape:
    /prbot                              → top-level help
    /prbot config                       → scope summary
    /prbot config <domain>              → domain help / summary
    /prbot config <domain> <action> …   → perform an action

Commands are integration-agnostic — they accept plain strings and return
plain strings (Slack mrkdwn). Integration layers only parse raw input
into (subcommand, args, scope_keys) and display the result.
"""

from __future__ import annotations

import logging
from typing import Protocol

from prbot.application.exclusions.manage_self_reviews import ManageSelfReviews
from prbot.application.exclusions.manage_user_exclusions import (
    ExclusionEntry,
    ExclusionResult,
    ManageUserExclusions,
)
from prbot.domain.emoji.ports import EmojiConfigResolverPort

logger = logging.getLogger(__name__)

# ─── Scope resolution helpers ─────────────────────────────────────

# scope_keys are ordered most-specific first: [channel, workspace, integration]
_SCOPE_LEVELS: dict[str, int] = {
    "channel": 0,
    "workspace": 1,
}
_SCOPE_LABEL_BY_INDEX: tuple[str, ...] = ("channel", "workspace", "global")


def _resolve_scope(scope_keys: list[str], scope_arg: str | None) -> str | None:
    """Pick a single scope key based on an optional level argument.

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


# ─── ConfigDomain protocol + implementations ──────────────────────


class ConfigDomain(Protocol):
    """A sub-domain of configuration.

    Domains are registered as top-level Commands (e.g. ``/prbot exclusions …``)
    and also exposed under the ``config`` Command for summary + backward-compat
    routing (``/prbot config exclusions …``).
    """

    name: str
    aliases: tuple[str, ...]
    usage: str

    def help_text(self) -> str: ...
    async def execute(self, args: list[str], scope_keys: list[str]) -> str: ...
    async def summary(self, scope_keys: list[str]) -> str | None: ...


_BOT_SUFFIX = "[bot]"


def _format_add_note(result: ExclusionResult) -> str | None:
    """Render an advisory note for an `exclusions add` based on the GitHub lookup.

    Returns None when no note is warranted (lookup disabled or unambiguous user match).
    """
    if result.lookup_failed:
        return "⚠ Could not verify with GitHub right now — exclusion saved anyway."
    ref = result.lookup
    if ref is None:
        return (
            f"⚠ No GitHub account matches `{result.username}` — "
            "kept anyway in case it appears later."
        )
    stored_has_suffix = result.username.lower().endswith(_BOT_SUFFIX)
    if ref.kind == "bot" and not stored_has_suffix:
        return (
            f"⚠ `{ref.login}` is a GitHub App — webhook senders arrive as "
            f"`{ref.login}[bot]`. Re-add with the `[bot]` suffix for the exclusion to match."
        )
    if ref.kind == "organization":
        return (
            f"⚠ `{ref.login}` is a GitHub organization, not a user account — "
            "it won't appear as a webhook sender."
        )
    return None


def _format_check_marker(entry: ExclusionEntry) -> str:
    """Single-line marker summarizing the live state of a stored exclusion."""
    if entry.lookup_failed:
        return "⚠ lookup failed"
    ref = entry.lookup
    if ref is None:
        return "⚠ not found on GitHub"
    stored_has_suffix = entry.username.lower().endswith(_BOT_SUFFIX)
    match ref.kind:
        case "bot" if not stored_has_suffix:
            return f"🤖 bot — will not match; webhook form is `{ref.login}[bot]`"
        case "bot":
            return "🤖 bot"
        case "organization":
            return "⚠ organization — will not match sender logins"
        case "user":
            return "✓ user"


class ExclusionsDomain:
    name = "exclusions"
    aliases: tuple[str, ...] = ()
    usage = "`exclusions <add|remove|list> [args]` — skip PR status updates from specific users"

    def __init__(self, manage: ManageUserExclusions) -> None:
        self._manage = manage

    def help_text(self) -> str:
        return (
            "*Exclusions* — skip PR status updates from specific GitHub users.\n\n"
            "*Actions:*\n"
            "• `add <username> [channel|workspace]` — exclude a user\n"
            "• `remove <username> [channel|workspace]` — re-include a user\n"
            "• `list [channel|workspace]` — show excluded users (inherited view by default)\n"
            "• `check [channel|workspace]` — re-verify excluded users against GitHub\n\n"
            "*Examples:*\n"
            "• `/prbot exclusions add Cursor workspace`\n"
            "• `/prbot exclusions check`"
        )

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        if not args:
            return self.help_text()
        action, *rest = args
        action = action.lower()
        if action == "add":
            return await self._add(rest, scope_keys)
        if action == "remove":
            return await self._remove(rest, scope_keys)
        if action == "list":
            return await self._list(rest, scope_keys)
        if action == "check":
            return await self._check(rest, scope_keys)
        return f"Unknown action `{action}`.\n\n{self.help_text()}"

    async def summary(self, scope_keys: list[str]) -> str | None:
        grouped = await self._manage.list_excluded_users(scope_keys)
        if not grouped:
            return None
        lines = ["*Excluded users:*"]
        for scope_key in scope_keys:
            users = grouped.get(scope_key)
            if not users:
                continue
            label = _label_for_scope(scope_keys, scope_key).capitalize()
            formatted = ", ".join(f"`{u}`" for u in users)
            lines.append(f"  • *{label}* (`{scope_key}`): {formatted}")
        return "\n".join(lines)

    async def _add(self, args: list[str], scope_keys: list[str]) -> str:
        if not args or len(args) > 2:
            return "Usage: `add <username> [channel|workspace]`"
        username = args[0]
        scope_key = _resolve_scope(scope_keys, args[1] if len(args) == 2 else None)
        if scope_key is None:
            return f"Unknown scope `{args[1]}`. Use `channel` or `workspace`."
        result = await self._manage.exclude_user(scope_key, username)
        if result.was_already:
            primary = f"`{result.username}` is already excluded in `{scope_key}`."
        else:
            primary = f"Excluded `{result.username}` from PR status updates in `{scope_key}`."
        note = _format_add_note(result)
        return f"{primary}\n{note}" if note else primary

    async def _remove(self, args: list[str], scope_keys: list[str]) -> str:
        if not args or len(args) > 2:
            return "Usage: `remove <username> [channel|workspace]`"
        username = args[0]
        scope_key = _resolve_scope(scope_keys, args[1] if len(args) == 2 else None)
        if scope_key is None:
            return f"Unknown scope `{args[1]}`. Use `channel` or `workspace`."
        result = await self._manage.include_user(scope_key, username)
        if result.was_already:
            return f"`{result.username}` is not excluded in `{scope_key}`."
        return f"Re-included `{result.username}` in PR status updates in `{scope_key}`."

    async def _list(self, args: list[str], scope_keys: list[str]) -> str:
        if len(args) > 1:
            return "Usage: `list [channel|workspace]`"
        target_scopes = _resolve_scopes(scope_keys, args[0] if args else None)
        if target_scopes is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        grouped = await self._manage.list_excluded_users(target_scopes)
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

    async def _check(self, args: list[str], scope_keys: list[str]) -> str:
        if len(args) > 1:
            return "Usage: `check [channel|workspace]`"
        target_scopes = _resolve_scopes(scope_keys, args[0] if args else None)
        if target_scopes is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        grouped = await self._manage.check_excluded_users(target_scopes)
        if not grouped:
            if len(target_scopes) == 1:
                return f"No users are excluded in `{target_scopes[0]}`."
            return "No users are excluded."
        lines = ["*Excluded users (verified against GitHub):*"]
        for scope_key in target_scopes:
            entries = grouped.get(scope_key)
            if not entries:
                continue
            label = _label_for_scope(scope_keys, scope_key).capitalize()
            lines.append(f"• *{label}* (`{scope_key}`):")
            for entry in entries:
                marker = _format_check_marker(entry)
                lines.append(f"    • `{entry.username}` — {marker}")
        return "\n".join(lines)


class SelfReviewsDomain:
    name = "self-reviews"
    aliases: tuple[str, ...] = ()
    usage = "`self-reviews <mute|unmute|status>` — suppress reactions on author's own PR comments"

    def __init__(self, manage: ManageSelfReviews) -> None:
        self._manage = manage

    def help_text(self) -> str:
        return (
            "*Self-reviews* — suppress the `commented` emoji when the PR author "
            "comments on their own PR.\n\n"
            "*Actions:*\n"
            "• `mute [channel|workspace]` — stop reacting to self-reviews at this scope\n"
            "• `unmute [channel|workspace]` — resume reacting\n"
            "• `status [channel|workspace]` — show current setting\n\n"
            "*Examples:*\n"
            "• `/prbot self-reviews mute workspace`\n"
            "• `/prbot self-reviews status`"
        )

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        if not args:
            return self.help_text()
        action, *rest = args
        action = action.lower()
        if action == "mute":
            return await self._mute(rest, scope_keys)
        if action == "unmute":
            return await self._unmute(rest, scope_keys)
        if action == "status":
            return await self._status(rest, scope_keys)
        return f"Unknown action `{action}`.\n\n{self.help_text()}"

    async def summary(self, scope_keys: list[str]) -> str | None:
        muted_at = await self._manage.muted_at(scope_keys)
        if muted_at is None:
            return None
        label = _label_for_scope(scope_keys, muted_at).capitalize()
        return f"*Self-reviews:* muted at *{label}* (`{muted_at}`)"

    async def _mute(self, args: list[str], scope_keys: list[str]) -> str:
        if len(args) > 1:
            return "Usage: `mute [channel|workspace]`"
        scope_key = _resolve_scope(scope_keys, args[0] if args else None)
        if scope_key is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        newly = await self._manage.mute(scope_key)
        if newly:
            return f"Muted self-reviews in `{scope_key}`."
        return f"Self-reviews are already muted in `{scope_key}`."

    async def _unmute(self, args: list[str], scope_keys: list[str]) -> str:
        if len(args) > 1:
            return "Usage: `unmute [channel|workspace]`"
        scope_key = _resolve_scope(scope_keys, args[0] if args else None)
        if scope_key is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        removed = await self._manage.unmute(scope_key)
        if removed:
            return f"Unmuted self-reviews in `{scope_key}`."
        return f"Self-reviews were not muted in `{scope_key}`."

    async def _status(self, args: list[str], scope_keys: list[str]) -> str:
        if len(args) > 1:
            return "Usage: `status [channel|workspace]`"
        target_scopes = _resolve_scopes(scope_keys, args[0] if args else None)
        if target_scopes is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        muted_at = await self._manage.muted_at(target_scopes)
        if muted_at is None:
            if len(target_scopes) == 1:
                return f"Self-reviews are not muted in `{target_scopes[0]}`."
            return "Self-reviews are not muted."
        label = _label_for_scope(scope_keys, muted_at).capitalize()
        return f"Self-reviews muted at *{label}* (`{muted_at}`)."


class EmojiDomain:
    name = "emoji"
    aliases: tuple[str, ...] = ()
    usage = "`emoji status [scope]` — show the emoji mapping applied to each PR status"

    def __init__(self, resolver: EmojiConfigResolverPort) -> None:
        self._resolver = resolver

    def help_text(self) -> str:
        return (
            "*Emoji* — emoji reactions applied to each PR status.\n\n"
            "*Actions:*\n"
            "• `status [channel|workspace]` — show effective emoji mapping\n\n"
            "Custom overrides are read-only here for now; contact an admin to change them."
        )

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        if not args:
            return self.help_text()
        action, *rest = args
        action = action.lower()
        if action == "status":
            return await self._status(rest, scope_keys)
        return f"Unknown action `{action}`.\n\n{self.help_text()}"

    async def summary(self, scope_keys: list[str]) -> str | None:
        emoji = await self._resolver.resolve(scope_keys)
        return (
            "*Emoji config:*\n"
            f"  merged: `{emoji.merged}`\n"
            f"  closed: `{emoji.closed}`\n"
            f"  approved: `{emoji.approved}`\n"
            f"  changes requested: `{emoji.changes_requested}`\n"
            f"  commented: `{emoji.commented}`"
        )

    async def _status(self, args: list[str], scope_keys: list[str]) -> str:
        if len(args) > 1:
            return "Usage: `status [channel|workspace]`"
        target_scopes = _resolve_scopes(scope_keys, args[0] if args else None)
        if target_scopes is None:
            return f"Unknown scope `{args[0]}`. Use `channel` or `workspace`."
        emoji = await self._resolver.resolve(target_scopes)
        return (
            f"*Emoji config for* `{target_scopes[0]}`*:*\n"
            f"  merged: `{emoji.merged}`\n"
            f"  closed: `{emoji.closed}`\n"
            f"  approved: `{emoji.approved}`\n"
            f"  changes requested: `{emoji.changes_requested}`\n"
            f"  commented: `{emoji.commented}`"
        )


# ─── ShowConfigCommand (nested dispatcher) ────────────────────────


class ShowConfigCommand:
    """Renders a summary across all domains and provides a backward-compat routing
    layer so existing ``/prbot config <domain> <action>`` invocations keep working
    after the domains were promoted to top-level Commands."""

    name = "config"
    aliases: tuple[str, ...] = ()
    usage = "`config` — show current scope's settings across all domains"

    def __init__(self, domains: list[ConfigDomain]) -> None:
        self._by_name: dict[str, ConfigDomain] = {d.name: d for d in domains}
        self._ordered: list[ConfigDomain] = domains

    async def execute(self, args: list[str], scope_keys: list[str]) -> str:
        if not args:
            return await self._summary(scope_keys)
        first, *rest = args
        domain = self._by_name.get(first.lower())
        if domain is None:
            return f"Unknown config domain `{first}`.\n\n{self._help_text()}"
        return await domain.execute(rest, scope_keys)

    async def _summary(self, scope_keys: list[str]) -> str:
        lines: list[str] = []
        if scope_keys:
            lines.append(f"*Scope:* `{scope_keys[0]}`")
        for domain in self._ordered:
            section = await domain.summary(scope_keys)
            if section:
                lines.append(section)
        lines.append("")
        lines.append("Type `/prbot <domain>` to see available actions.")
        return "\n".join(lines)

    def _help_text(self) -> str:
        lines = [
            "*Configuration*",
            "Use `/prbot <domain> <action>` to change scope-level settings.",
            "",
            "*Domains:*",
        ]
        for d in self._ordered:
            first_line = d.help_text().split("\n", 1)[0]
            lines.append(f"• `{d.name}` — {first_line.split('—', 1)[-1].strip() or first_line}")
        lines.append("")
        lines.append("Type `/prbot <domain>` for that domain's actions.")
        return "\n".join(lines)


# ─── Top-level Command protocol + dispatcher ──────────────────────


class Command(Protocol):
    """A top-level slash-command subcommand."""

    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> tuple[str, ...]: ...

    @property
    def usage(self) -> str: ...

    async def execute(self, args: list[str], scope_keys: list[str]) -> str: ...


class CommandDispatcher:
    """Routes the first token of /prbot input to a Command."""

    def __init__(self, commands: list[Command]) -> None:
        self._commands: dict[str, Command] = {}
        self._ordered: list[Command] = commands
        for cmd in commands:
            self._commands[cmd.name] = cmd
            for alias in cmd.aliases:
                self._commands[alias] = cmd

    async def dispatch(self, subcommand: str, args: list[str], scope_keys: list[str]) -> str:
        cmd = self._commands.get(subcommand.lower())
        if cmd is None:
            return self._help_text()
        return await cmd.execute(args, scope_keys)

    def _help_text(self) -> str:
        lines = ["*prbot commands*", ""]
        for cmd in self._ordered:
            lines.append(f"• {cmd.usage}")
        return "\n".join(lines)


def build_default_dispatcher(
    manage_exclusions: ManageUserExclusions,
    manage_self_reviews: ManageSelfReviews,
    emoji_resolver: EmojiConfigResolverPort,
) -> CommandDispatcher:
    """Build the standard dispatcher.

    Every domain is a top-level Command (``/prbot exclusions …``) and also
    reachable via the ``config`` command for both a cross-domain summary
    (``/prbot config``) and backward-compat routing of the old shape
    (``/prbot config <domain> …``).
    """
    exclusions = ExclusionsDomain(manage_exclusions)
    self_reviews = SelfReviewsDomain(manage_self_reviews)
    emoji = EmojiDomain(emoji_resolver)
    config_cmd = ShowConfigCommand([exclusions, self_reviews, emoji])
    return CommandDispatcher([config_cmd, exclusions, self_reviews, emoji])
