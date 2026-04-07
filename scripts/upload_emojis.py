#!/usr/bin/env python3
"""Upload prbot custom emoji to Slack or Discord.

Usage:
    # Slack – requires an admin-level token (xoxp-...) with admin.emoji:write scope
    python scripts/upload_emojis.py slack --token xoxp-...

    # Discord – requires a bot token with Manage Guild Expressions permission
    python scripts/upload_emojis.py discord --token Bot-TOKEN --guild-id 123456789

Emoji images are read from the docs/images/emojis/ directory relative to this script.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

EMOJIS_DIR = Path(__file__).resolve().parent.parent / "docs" / "images" / "emojis"

MIME_TYPES = {
    ".png": "image/png",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

console = Console()


def discover_emojis() -> list[tuple[str, Path]]:
    """Return (name, path) pairs for every emoji image in the emojis/ directory."""
    emojis: list[tuple[str, Path]] = []
    for path in sorted(EMOJIS_DIR.iterdir()):
        if path.suffix.lower() in MIME_TYPES:
            emojis.append((path.stem, path))
    return emojis


def build_results_table(
    platform: str,
    results: list[tuple[str, str]],
) -> Table:
    """Build a rich table summarising upload results."""
    table = Table(title=f"Emoji upload — {platform}")
    table.add_column("Emoji", style="cyan")
    table.add_column("Status")

    status_styles = {
        "uploaded": "[green]uploaded[/green]",
        "skipped": "[yellow]already exists[/yellow]",
    }

    for name, status in results:
        styled = status_styles.get(status, f"[red]{status}[/red]")
        table.add_row(f":{name}:", styled)

    return table


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def upload_slack(token: str) -> None:
    emojis = discover_emojis()
    if not emojis:
        console.print("[red]No emoji images found in[/red]", EMOJIS_DIR)
        sys.exit(1)

    results: list[tuple[str, str]] = []

    with console.status("Uploading emoji to Slack..."), httpx.Client() as client:
        for name, path in emojis:
            resp = client.post(
                "https://slack.com/api/emoji.add",
                headers={"Authorization": f"Bearer {token}"},
                data={"name": name, "mode": "data"},
                files={"image": (path.name, path.read_bytes(), MIME_TYPES[path.suffix.lower()])},
            )
            body = resp.json()

            if body.get("ok"):
                results.append((name, "uploaded"))
            elif body.get("error") == "error_name_taken":
                results.append((name, "skipped"))
            else:
                results.append((name, f"FAILED – {body.get('error', resp.text)}"))

    console.print(build_results_table("Slack", results))


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


def upload_discord(token: str, guild_id: str) -> None:
    emojis = discover_emojis()
    if not emojis:
        console.print("[red]No emoji images found in[/red]", EMOJIS_DIR)
        sys.exit(1)

    results: list[tuple[str, str]] = []

    status_msg = f"Uploading emoji to Discord guild {guild_id}..."
    with console.status(status_msg), httpx.Client() as client:
        for name, path in emojis:
            mime = MIME_TYPES[path.suffix.lower()]
            data_uri = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"

            resp = client.post(
                f"https://discord.com/api/v10/guilds/{guild_id}/emojis",
                headers={"Authorization": f"Bot {token}"},
                json={"name": name, "image": data_uri},
            )

            if resp.status_code == 201:
                results.append((name, "uploaded"))
            elif resp.status_code == 400 and "already" in resp.text.lower():
                results.append((name, "skipped"))
            else:
                results.append((name, f"FAILED ({resp.status_code}) – {resp.text}"))

    console.print(build_results_table("Discord", results))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload prbot emoji to Slack or Discord")
    sub = parser.add_subparsers(dest="platform", required=True)

    slack = sub.add_parser("slack", help="Upload emoji to a Slack workspace")
    slack.add_argument(
        "--token",
        required=True,
        help="Slack admin token (xoxp-...) with admin.emoji:write scope",
    )

    discord = sub.add_parser("discord", help="Upload emoji to a Discord server")
    discord.add_argument("--token", required=True, help="Discord bot token")
    discord.add_argument("--guild-id", required=True, help="Discord guild (server) ID")

    args = parser.parse_args()

    if args.platform == "slack":
        upload_slack(args.token)
    elif args.platform == "discord":
        upload_discord(args.token, args.guild_id)


if __name__ == "__main__":
    main()
