"""Invite external customer users into #userled-community.

Walks every customer channel in the `orgs` table, collects the external
(Slack Connect) members, and invites the ones not already in the community
channel. The `community_invites` ledger makes it safe to re-run: anyone the
sweep has seen before — invited, already a member, or who has ever left the
community channel — is skipped forever. Only `failed` invites are retried.

Each run first syncs the community channel into the ledger:
  - current members → `already_member` (existing rows are left alone)
  - every `channel_leave` in its history → `left`
so people who left before this script existed, or who joined and left between
sweeps, are protected too.

Reuses the app's bot token (`CUSTOMERBOT_SLACK__BOT_TOKEN`) and DB path
(`CUSTOMERBOT_DATABASE_PATH`), so on the Fly machine it targets the live volume.

Requires:
  - the bot is a member of the community channel (add it once, by hand)
  - scopes groups:read, groups:history, groups:write, channels:read,
    users:read, users:read.email (all already in slack-manifest.yml)

`conversations.invite` only works for users whose org is already connected to
the community channel. Everyone else comes back `failed`; the summary lists
those orgs so someone can share the channel with them manually.

Usage (on the Fly machine, via `fly ssh console -a customerbot-userled`):

    uv run --no-sync python scripts/add_customers_to_community_channel.py --dry-run
    uv run --no-sync python scripts/add_customers_to_community_channel.py
    uv run --no-sync python scripts/add_customers_to_community_channel.py --org "Acme"
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import defaultdict
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_async_handlers import AsyncRateLimitErrorRetryHandler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.community.sweep import (
    ALREADY_MEMBER,
    FAILED,
    INVITED,
    LEFT,
    Candidate,
    CommunityInvite,
    parse_leavers,
    select_candidates,
)
from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
)
from customerbot.data.repository.community_invites import SQLiteCommunityInviteRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository

COMMUNITY_CHANNEL_ID = "C083LN276V7"  # #userled-community


def _error(e: SlackApiError) -> str:
    return str(e.response.get("error", "unknown_error"))


async def _channel_members(client: AsyncWebClient, channel_id: str) -> list[str]:
    members: list[str] = []
    async for page in await client.conversations_members(channel=channel_id, limit=1000):
        members.extend(page.get("members", []))
    return members


async def _channel_history(client: AsyncWebClient, channel_id: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    async for page in await client.conversations_history(channel=channel_id, limit=999):
        messages.extend(page.get("messages", []))
    return messages


async def _users_info(client: AsyncWebClient, user_ids: set[str]) -> dict[str, dict[str, Any]]:
    users: dict[str, dict[str, Any]] = {}
    for uid in sorted(user_ids):
        try:
            resp = await client.users_info(user=uid)
            users[uid] = resp["user"]
        except SlackApiError as e:
            print(f"  WARNING: users.info {uid} failed: {_error(e)}")
    return users


async def _invite_one(client: AsyncWebClient, channel_id: str, user_id: str) -> tuple[str, str]:
    """Invite one user. Returns (ledger status, detail)."""
    try:
        await client.conversations_invite(channel=channel_id, users=user_id)
        return (INVITED, "")
    except SlackApiError as e:
        error = _error(e)
        if error == "already_in_channel":
            return (ALREADY_MEMBER, "")
        return (FAILED, error)


async def _run(args: argparse.Namespace) -> None:
    token = os.environ.get("CUSTOMERBOT_SLACK__BOT_TOKEN")
    if not token:
        raise SystemExit("CUSTOMERBOT_SLACK__BOT_TOKEN is not set in the environment")

    db_path = os.environ.get("CUSTOMERBOT_DATABASE_PATH", "data/customerbot.db")
    engine = make_engine(database_url_from_path(db_path))
    factory = make_session_factory(engine)
    try:
        await _sweep(args, AsyncWebClient(token=token), factory, db_path)
    finally:
        await engine.dispose()


async def _sweep(
    args: argparse.Namespace,
    client: AsyncWebClient,
    factory: async_sessionmaker[AsyncSession],
    db_path: str,
) -> None:
    client.retry_handlers.append(AsyncRateLimitErrorRetryHandler(max_retry_count=5))
    ledger = SQLiteCommunityInviteRepository(factory)
    community = args.channel
    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"[{mode}] community channel {community}, db {db_path}\n")

    home_team_id = (await client.auth_test())["team_id"]

    # 1. Sync the community channel into the ledger.
    try:
        community_members = set(await _channel_members(client, community))
        leavers = parse_leavers(await _channel_history(client, community))
    except SlackApiError as e:
        raise SystemExit(
            f"Can't read {community}: {_error(e)}. The bot must be a member of the "
            "community channel — add it by hand, then re-run."
        ) from e
    leavers -= community_members  # rejoined since — they're members, still blocked
    print(f"Community channel: {len(community_members)} member(s), {len(leavers)} past leaver(s)")

    if not args.dry_run:
        for uid in community_members:
            await ledger.record(
                CommunityInvite(user_id=uid, status=ALREADY_MEMBER), overwrite=False
            )
        for uid in leavers:
            await ledger.record(
                CommunityInvite(user_id=uid, status=LEFT, detail="channel_leave in history")
            )
    blocked = await ledger.blocked_user_ids() | leavers
    print(f"Ledger: {len(blocked)} user(s) blocked from re-invite\n")

    # 2. Customer channels from the orgs table.
    orgs = await SQLiteOrgRepository(factory).list_all()
    channels: dict[str, str] = {}
    for org in orgs:
        if args.org and org.name.lower() != args.org.lower():
            continue
        if org.slack_channel_id and org.slack_channel_id not in channels:
            channels[org.slack_channel_id] = org.name
    if not channels:
        target = f"org {args.org!r}" if args.org else "any org"
        print(f"No customer channels found for {target}. Nothing to do.")
        return

    channel_members: list[tuple[str, str, list[str]]] = []
    unreadable: list[str] = []
    for channel_id, name in channels.items():
        try:
            channel_members.append((channel_id, name, await _channel_members(client, channel_id)))
        except SlackApiError as e:
            unreadable.append(f"{name} ({channel_id}): {_error(e)}")
    print(f"Customer channels: {len(channel_members)} read, {len(unreadable)} unreadable")

    # 3. Look up only users who could still be candidates.
    to_look_up = {
        uid
        for _, _, members in channel_members
        for uid in members
        if uid not in blocked and uid not in community_members
    }
    users = await _users_info(client, to_look_up)
    candidates = select_candidates(
        channel_members,
        users,
        home_team_id=home_team_id,
        blocked=blocked,
        community_members=community_members,
    )

    by_org: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        by_org[c.org].append(c)
    print(f"\n{len(candidates)} external user(s) to invite across {len(by_org)} org(s):\n")

    if args.dry_run:
        for org_name in sorted(by_org):
            print(f"  {org_name} ({len(by_org[org_name])})")
            for c in by_org[org_name]:
                print(f"    would invite {c.name} <{c.email or 'no email'}> ({c.user_id})")
        _print_unreadable(unreadable)
        print("\nDry run — nothing written. Re-run without --dry-run to invite.")
        return

    # 4. Invite, one user per call so one bad user can't fail the batch.
    counts = {INVITED: 0, ALREADY_MEMBER: 0, FAILED: 0}
    failed_orgs: dict[str, set[str]] = defaultdict(set)
    for org_name in sorted(by_org):
        print(f"  {org_name}")
        for c in by_org[org_name]:
            status, detail = await _invite_one(client, community, c.user_id)
            counts[status] += 1
            await ledger.record(
                CommunityInvite(
                    user_id=c.user_id,
                    status=status,
                    team_id=c.team_id,
                    email=c.email,
                    source_org=c.org,
                    source_channel_id=c.channel_id,
                    detail=detail or None,
                )
            )
            if status == FAILED:
                failed_orgs[org_name].add(detail)
            marker = {INVITED: "✓ invited", ALREADY_MEMBER: "· already in", FAILED: "✗ FAILED"}[
                status
            ]
            suffix = f" — {detail}" if detail else ""
            print(f"    {marker}: {c.name} <{c.email or 'no email'}>{suffix}")

    print(
        f"\nDone. invited={counts[INVITED]} already={counts[ALREADY_MEMBER]} "
        f"failed={counts[FAILED]} blocked={len(blocked)}"
    )
    if failed_orgs:
        print(
            "\nOrgs with failed invites — usually not yet connected to the community "
            "channel; share it with them via Slack Connect, then re-run:"
        )
        for org_name in sorted(failed_orgs):
            print(f"  • {org_name}: {', '.join(sorted(failed_orgs[org_name]))}")
    _print_unreadable(unreadable)

    if counts[FAILED]:
        raise SystemExit(1)


def _print_unreadable(unreadable: list[str]) -> None:
    if unreadable:
        print("\nCustomer channels the bot couldn't read (add the bot, then re-run):")
        for line in unreadable:
            print(f"  • {line}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invite external customer users from the customer channels "
        "into #userled-community. Never re-adds anyone who has left."
    )
    parser.add_argument(
        "--channel",
        default=COMMUNITY_CHANNEL_ID,
        help=f"Community channel ID (default {COMMUNITY_CHANNEL_ID}, #userled-community)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List who would be invited; no invites and no DB writes",
    )
    parser.add_argument("--org", help="Only sweep this customer (orgs.name, case-insensitive)")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
