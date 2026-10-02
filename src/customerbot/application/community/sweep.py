"""Pure selection logic for the #userled-community sweep.

`scripts/add_customers_to_community_channel.py` does the Slack and DB I/O; this
module decides who to invite, so the rules can be tested without Slack:

- only external customer users (another Slack team, not a bot, not deleted)
- never anyone the ledger has already seen, unless their last attempt `failed`
- never anyone who has ever left the community channel
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

INVITED = "invited"
ALREADY_MEMBER = "already_member"
LEFT = "left"
FAILED = "failed"

# Every ledger status except FAILED means "never invite this user again".
BLOCKING_STATUSES = frozenset({INVITED, ALREADY_MEMBER, LEFT})

# Join/leave message subtypes in conversations.history. Private channels
# created before Slack's 2021 conversation unification used the `group_` form.
_LEAVE_SUBTYPES = frozenset({"channel_leave", "group_leave"})


@dataclass(frozen=True)
class CommunityInvite:
    user_id: str
    status: str
    team_id: str | None = None
    email: str | None = None
    source_org: str | None = None
    source_channel_id: str | None = None
    detail: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Candidate:
    """An external customer user eligible for a community invite."""

    user_id: str
    team_id: str
    email: str | None
    name: str
    org: str
    channel_id: str


def is_external_user(user: Mapping[str, Any], home_team_id: str) -> bool:
    """True for a real (human, active) user who belongs to another Slack team."""
    if user.get("deleted") or user.get("is_bot") or user.get("is_app_user"):
        return False
    if user.get("id") == "USLACKBOT":
        return False
    team_id = user.get("team_id")
    return bool(team_id) and team_id != home_team_id


def select_candidates(
    channel_members: Iterable[tuple[str, str, Iterable[str]]],
    users: Mapping[str, Mapping[str, Any]],
    *,
    home_team_id: str,
    blocked: Iterable[str],
    community_members: Iterable[str],
) -> list[Candidate]:
    """Pick who to invite.

    `channel_members` is `(channel_id, org_name, member_ids)` per customer
    channel. `users` maps user ID → `users.info` payload; members missing from
    it are skipped. A user in several customer channels is attributed to the
    first one seen.
    """
    skip = set(blocked) | set(community_members)
    seen: set[str] = set()
    out: list[Candidate] = []
    for channel_id, org, member_ids in channel_members:
        for uid in member_ids:
            if uid in seen or uid in skip:
                continue
            user = users.get(uid)
            if user is None or not is_external_user(user, home_team_id):
                continue
            seen.add(uid)
            profile = user.get("profile") or {}
            out.append(
                Candidate(
                    user_id=uid,
                    team_id=user["team_id"],
                    email=profile.get("email"),
                    name=profile.get("real_name")
                    or user.get("real_name")
                    or user.get("name")
                    or uid,
                    org=org,
                    channel_id=channel_id,
                )
            )
    return out


def parse_leavers(messages: Iterable[Mapping[str, Any]]) -> set[str]:
    """User IDs with a leave event in the community channel's history.

    Leaving is sticky: a later rejoin doesn't clear it. Rejoined users are
    current members anyway, so they're blocked either way.
    """
    return {m["user"] for m in messages if m.get("subtype") in _LEAVE_SUBTYPES and m.get("user")}
