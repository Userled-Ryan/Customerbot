#!/usr/bin/env python3
"""Upload prbot custom emoji to Slack or Discord.

Usage:
    # Slack – requires an admin-level token (xoxp-...) with admin.emoji:write scope
    python scripts/upload_emojis.py slack --token xoxp-...

    # Discord – requires a bot token with Manage Guild Expressions permission
    python scripts/upload_emojis.py discord --token Bot-TOKEN --guild-id 123456789

Emoji images are read from the emojis/ directory relative to this script.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

import httpx

EMOJIS_DIR = Path(__file__).resolve().parent.parent / "emojis"

MIME_TYPES = {
    ".png": "image/png",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def discover_emojis() -> list[tuple[str, Path]]:
    """Return (name, path) pairs for every emoji image in the emojis/ directory."""
    emojis: list[tuple[str, Path]] = []
    for path in sorted(EMOJIS_DIR.iterdir()):
        if path.suffix.lower() in MIME_TYPES:
            emojis.append((path.stem, path))
    return emojis


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def upload_slack(token: str) -> None:
    emojis = discover_emojis()
    if not emojis:
        print("No emoji images found in", EMOJIS_DIR)
        sys.exit(1)

    print(f"Uploading {len(emojis)} emoji to Slack...\n")

    with httpx.Client() as client:
        for name, path in emojis:
            resp = client.post(
                "https://slack.com/api/emoji.add",
                headers={"Authorization": f"Bearer {token}"},
                data={"name": name, "mode": "data"},
                files={"image": (path.name, path.read_bytes(), MIME_TYPES[path.suffix.lower()])},
            )
            body = resp.json()

            if body.get("ok"):
                print(f"  :{name}: uploaded")
            elif body.get("error") == "error_name_taken":
                print(f"  :{name}: already exists, skipping")
            else:
                print(f"  :{name}: FAILED – {body.get('error', resp.text)}")


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


def upload_discord(token: str, guild_id: str) -> None:
    emojis = discover_emojis()
    if not emojis:
        print("No emoji images found in", EMOJIS_DIR)
        sys.exit(1)

    print(f"Uploading {len(emojis)} emoji to Discord guild {guild_id}...\n")

    with httpx.Client() as client:
        for name, path in emojis:
            mime = MIME_TYPES[path.suffix.lower()]
            data_uri = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"

            resp = client.post(
                f"https://discord.com/api/v10/guilds/{guild_id}/emojis",
                headers={"Authorization": f"Bot {token}"},
                json={"name": name, "image": data_uri},
            )

            if resp.status_code == 201:
                print(f"  :{name}: uploaded")
            elif resp.status_code == 400 and "already" in resp.text.lower():
                print(f"  :{name}: already exists, skipping")
            else:
                print(f"  :{name}: FAILED ({resp.status_code}) – {resp.text}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload prbot emoji to Slack or Discord")
    sub = parser.add_subparsers(dest="platform", required=True)

    slack = sub.add_parser("slack", help="Upload emoji to a Slack workspace")
    slack.add_argument(
        "--token", required=True, help="Slack admin token (xoxp-...) with admin.emoji:write scope"
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
