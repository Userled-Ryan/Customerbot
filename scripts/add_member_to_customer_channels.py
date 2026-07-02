"""Add a Userled teammate to every customer Slack channel.

The `orgs` table is customerbot's live customer list — each row maps a customer
to their Slack channel. When a new teammate joins, this script walks that list
and invites them to each mapped channel, so the roster stays in sync with
whatever customers currently exist (churned customers drop off automatically).

Reuses the app's own bot token (`CUSTOMERBOT_SLACK__BOT_TOKEN`) and DB path
(`CUSTOMERBOT_DATABASE_PATH`), so on the Fly machine it targets the live volume
and the installed bot — no local secrets required.

Requires the bot to hold these scopes (see slack-manifest.yml):
    channels:write.invites   invite to public channels
    groups:write             invite to private channels
    channels:join            self-join a public channel it isn't in yet
    users:read.email         resolve --email to a user ID

The bot can only invite into channels it is already a member of. It self-joins
public channels as needed; private / Slack Connect channels it isn't in are
reported as failures for a human to handle.

Usage (on the Fly machine, via `fly ssh console -a customerbot-userled`):

    uv run --no-sync python scripts/add_member_to_customer_channels.py \
        --email newhire@userled.io --dry-run

    uv run --no-sync python scripts/add_member_to_customer_channels.py \
        --email newhire@userled.io
"""

from __future__ import annotations

import argparse
import asyncio
import os

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
)
from customerbot.data.repository.orgs import SQLiteOrgRepository

# Slack errors that mean "nothing to do here" rather than a real failure.
_OK_ERRORS = {"already_in_channel"}


async def _resolve_user_id(client: AsyncWebClient, args: argparse.Namespace) -> str:
    if args.user_id:
        return args.user_id
    resp = await client.users_lookupByEmail(email=args.email)
    return resp["user"]["id"]


async def _invite_one(
    client: AsyncWebClient, channel_id: str, user_id: str
) -> tuple[str, str]:
    """Invite user to one channel. Returns (status, detail).

    status is one of: invited, already, skipped, failed.
    """
    for attempt in range(2):  # second pass is a retry after a self-join
        try:
            await client.conversations_invite(channel=channel_id, users=user_id)
            return ("invited", "")
        except SlackApiError as e:
            error = e.response.get("error", "unknown_error")
            if error == "already_in_channel":
                return ("already", "")
            if error == "is_archived":
                return ("skipped", "channel archived")
            if error == "ratelimited":
                retry_after = int(e.response.headers.get("Retry-After", "2"))
                await asyncio.sleep(retry_after)
                continue
            if error == "not_in_channel" and attempt == 0:
                # Bot isn't a member. Try to self-join (public channels only),
                # then retry the invite on the next loop pass.
                try:
                    await client.conversations_join(channel=channel_id)
                    continue
                except SlackApiError as join_err:
                    return (
                        "failed",
                        f"bot not in channel, join failed: "
                        f"{join_err.response.get('error', 'unknown')}",
                    )
            return ("failed", error)
    return ("failed", "gave up after retries")


async def _dm_missing(client: AsyncWebClient, user_id: str, missing: list[str]) -> None:
    """DM the user the customer channels we couldn't add them to."""
    bullets = "\n".join(f"• {name}" for name in missing)
    text = (
        "👋 Welcome! You've been added to Userled's customer Slack channels.\n\n"
        f"I couldn't add you to the {len(missing)} below automatically — they're "
        "externally-owned by the customer, so someone already in the channel needs "
        "to add you manually. Worth asking to be added to:\n\n"
        f"{bullets}"
    )
    opened = await client.conversations_open(users=user_id)
    await client.chat_postMessage(channel=opened["channel"]["id"], text=text)


async def _run(args: argparse.Namespace) -> None:
    db_path = os.environ.get("CUSTOMERBOT_DATABASE_PATH", "data/customerbot.db")
    engine = make_engine(database_url_from_path(db_path))
    factory = make_session_factory(engine)
    orgs_repo = SQLiteOrgRepository(factory)

    orgs = await orgs_repo.list_all()
    await engine.dispose()

    # Distinct channels, keeping the first org name we saw for each.
    channels: dict[str, str] = {}
    for org in orgs:
        if org.slack_channel_id and org.slack_channel_id not in channels:
            channels[org.slack_channel_id] = org.name

    if not channels:
        print(f"No customer channels found in {db_path}. Nothing to do.")
        return

    who = args.email or args.user_id
    print(f"Customer channels in {db_path}: {len(channels)}\n")

    # Dry run is pure DB read — no Slack token or scopes required.
    if args.dry_run:
        for channel_id, name in channels.items():
            print(f"  would invite {who} → {name} ({channel_id})")
        print(f"\nDry run: {len(channels)} channel(s). Re-run without --dry-run to apply.")
        return

    token = os.environ.get("CUSTOMERBOT_SLACK__BOT_TOKEN")
    if not token:
        raise SystemExit("CUSTOMERBOT_SLACK__BOT_TOKEN is not set in the environment")

    client = AsyncWebClient(token=token)
    user_id = await _resolve_user_id(client, args)
    print(f"Target user: {who} ({user_id})\n")

    counts = {"invited": 0, "already": 0, "skipped": 0, "failed": 0}
    not_added: list[str] = []
    for channel_id, name in channels.items():
        status, detail = await _invite_one(client, channel_id, user_id)
        counts[status] += 1
        if status == "failed":
            not_added.append(name)
        marker = {
            "invited": "✓ invited",
            "already": "· already in",
            "skipped": "– skipped",
            "failed": "✗ FAILED",
        }[status]
        suffix = f" — {detail}" if detail else ""
        print(f"  {marker}: {name} ({channel_id}){suffix}")

    print(
        f"\nDone. invited={counts['invited']} already={counts['already']} "
        f"skipped={counts['skipped']} failed={counts['failed']}"
    )

    if not_added and not args.no_dm:
        try:
            await _dm_missing(client, user_id, not_added)
            print(f"DM'd {who} the {len(not_added)} channel(s) to request manually.")
        except SlackApiError as e:
            print(f"WARNING: couldn't DM the user: {e.response.get('error', 'unknown')}")

    if counts["failed"]:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a teammate to every customer Slack channel in the orgs table."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", help="Teammate's Slack email (resolved to a user ID)")
    group.add_argument("--user-id", help="Teammate's Slack user ID (U…), skips lookup")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the channels that would be targeted without inviting anyone",
    )
    parser.add_argument(
        "--no-dm",
        action="store_true",
        help="Don't DM the user the list of channels they couldn't be added to",
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
