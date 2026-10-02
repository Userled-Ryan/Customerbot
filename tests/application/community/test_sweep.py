from __future__ import annotations

from typing import Any

from customerbot.application.community.sweep import (
    is_external_user,
    parse_leavers,
    select_candidates,
)

HOME = "T_USERLED"


def _user(uid: str, team: str = "T_ACME", **extra: object) -> dict[str, Any]:
    return {
        "id": uid,
        "team_id": team,
        "profile": {"email": f"{uid.lower()}@acme.com", "real_name": f"User {uid}"},
        **extra,
    }


def test_is_external_user_filters_home_team_bots_and_deleted() -> None:
    assert is_external_user(_user("U1"), HOME)
    assert not is_external_user(_user("U2", team=HOME), HOME)
    assert not is_external_user(_user("U3", is_bot=True), HOME)
    assert not is_external_user(_user("U4", is_app_user=True), HOME)
    assert not is_external_user(_user("U5", deleted=True), HOME)
    assert not is_external_user(_user("USLACKBOT"), HOME)
    assert not is_external_user({"id": "U6"}, HOME)  # no team_id


def test_select_candidates_skips_blocked_members_and_internal_users() -> None:
    users = {
        "U_NEW": _user("U_NEW"),
        "U_LEFT": _user("U_LEFT"),
        "U_MEMBER": _user("U_MEMBER"),
        "U_STAFF": _user("U_STAFF", team=HOME),
        "U_BOT": _user("U_BOT", is_bot=True),
    }
    picked = select_candidates(
        [("C_ACME", "Acme", ["U_NEW", "U_LEFT", "U_MEMBER", "U_STAFF", "U_BOT", "U_UNKNOWN"])],
        users,
        home_team_id=HOME,
        blocked={"U_LEFT"},
        community_members={"U_MEMBER"},
    )
    assert [c.user_id for c in picked] == ["U_NEW"]
    c = picked[0]
    assert (c.org, c.channel_id, c.team_id) == ("Acme", "C_ACME", "T_ACME")
    assert c.email == "u_new@acme.com"
    assert c.name == "User U_NEW"


def test_select_candidates_dedupes_user_across_channels_first_wins() -> None:
    users = {"U1": _user("U1")}
    picked = select_candidates(
        [("C_A", "Acme", ["U1"]), ("C_B", "Acme EU", ["U1"])],
        users,
        home_team_id=HOME,
        blocked=set(),
        community_members=set(),
    )
    assert [(c.user_id, c.org) for c in picked] == [("U1", "Acme")]


def test_parse_leavers_reads_leave_subtypes_only() -> None:
    history = [
        {"subtype": "channel_join", "user": "U_JOIN"},
        {"subtype": "channel_leave", "user": "U_LEFT"},
        {"subtype": "group_leave", "user": "U_OLD_LEFT"},
        {"text": "hello", "user": "U_CHAT"},
        {"subtype": "channel_leave"},  # malformed, no user
    ]
    assert parse_leavers(history) == {"U_LEFT", "U_OLD_LEFT"}
