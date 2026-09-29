"""Tests for the `/ooo` modal: view shape + submission parsing."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from customerbot.domain.tickets.entities import SeAbsence
from customerbot.integration.slack.modals import ooo
from customerbot.integration.slack.modals.submission_payload import parse_ooo


def _view(
    *,
    who: str | None = "U_ELIZA",
    status: str = ooo.STATUS_OUT,
    back_on: str | None = "2026-10-03",
    cover: str | None = "U_SAM",
    move_open: bool = True,
    metadata: str | None = None,
) -> dict[str, Any]:
    if metadata is None:
        metadata = json.dumps({"channel_id": "C_HERE", "user_id": "U_SE"})
    moves = [{"value": ooo.MOVE_OPEN_VALUE}] if move_open else []
    return {
        "state": {
            "values": {
                ooo.BLOCK_WHO: {ooo.ACTION_WHO: {"selected_user": who}},
                ooo.BLOCK_STATUS: {ooo.ACTION_STATUS: {"selected_option": {"value": status}}},
                ooo.BLOCK_BACK_ON: {ooo.ACTION_BACK_ON: {"selected_date": back_on}},
                ooo.BLOCK_COVER: {ooo.ACTION_COVER: {"selected_user": cover}},
                ooo.BLOCK_MOVE_OPEN: {ooo.ACTION_MOVE_OPEN: {"selected_options": moves}},
            }
        },
        "private_metadata": metadata,
    }


def test_parse_ooo_mark_out() -> None:
    sub = parse_ooo(_view())
    assert sub.channel_id == "C_HERE"
    assert sub.by_user_id == "U_SE"
    assert sub.user_id == "U_ELIZA"
    assert sub.back is False
    assert sub.back_on == date(2026, 10, 3)
    assert sub.cover_user_id == "U_SAM"
    assert sub.move_open is True


def test_parse_ooo_optional_fields_blank() -> None:
    sub = parse_ooo(_view(back_on=None, cover=None, move_open=False))
    assert sub.back_on is None
    assert sub.cover_user_id is None
    assert sub.move_open is False


def test_parse_ooo_mark_back() -> None:
    assert parse_ooo(_view(status=ooo.STATUS_BACK)).back is True


def test_parse_ooo_rejects_missing_who_and_bad_metadata() -> None:
    with pytest.raises(ValueError):
        parse_ooo(_view(who=None))
    with pytest.raises(ValueError):
        parse_ooo(_view(metadata="nope"))


def _action_ids(node: Any) -> list[str]:  # noqa: ANN401 — walks arbitrary Block Kit JSON
    if isinstance(node, dict):
        own = [node["action_id"]] if "action_id" in node else []
        return own + [a for v in node.values() for a in _action_ids(v)]
    if isinstance(node, list):
        return [a for v in node for a in _action_ids(v)]
    return []


def test_build_view_lists_absences_and_prefills_invoker() -> None:
    view = ooo.build_view(
        channel_id="C_HERE",
        user_id="U_SE",
        absences=[SeAbsence(user_id="U_ELIZA", cover_user_id="U_SAM", back_on=date(2026, 10, 3))],
    )
    assert view["callback_id"] == ooo.CALLBACK_ID
    assert json.loads(view["private_metadata"]) == {"channel_id": "C_HERE", "user_id": "U_SE"}
    summary = view["blocks"][0]["text"]["text"]
    assert "<@U_ELIZA> is out until Sat 3 Oct" in summary and "<@U_SAM>" in summary
    who = next(b for b in view["blocks"] if b.get("block_id") == ooo.BLOCK_WHO)
    assert who["element"]["initial_user"] == "U_SE"
    # Duplicate action_ids make Slack reject the view (invalid_blocks).
    ids = _action_ids(view["blocks"])
    assert len(ids) == len(set(ids)) == 5


def test_build_view_with_nobody_out() -> None:
    view = ooo.build_view(channel_id="C", user_id="U_SE", absences=[])
    assert "Nobody is marked out" in view["blocks"][0]["text"]["text"]
