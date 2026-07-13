"""Tests for the `/report` date-range modal: parsing + default window."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
import time_machine

from customerbot.integration.slack.handler import _default_report_range
from customerbot.integration.slack.modals import report_range
from customerbot.integration.slack.modals.submission_payload import parse_report_range


def _range_view(
    *,
    start: str | None = "2026-07-06",
    end: str | None = "2026-07-10",
    channel_id: str = "C_CUST",
    user_id: str = "U_SE",
    metadata: str | None = None,
) -> dict[str, Any]:
    if metadata is None:
        metadata = json.dumps({"channel_id": channel_id, "user_id": user_id})
    return {
        "state": {
            "values": {
                report_range.BLOCK_START: {report_range.ACTION_START: {"selected_date": start}},
                report_range.BLOCK_END: {report_range.ACTION_END: {"selected_date": end}},
            }
        },
        "private_metadata": metadata,
    }


def test_parse_report_range_round_trip() -> None:
    channel_id, user_id, start, end = parse_report_range(_range_view())
    assert channel_id == "C_CUST"
    assert user_id == "U_SE"
    assert start == date(2026, 7, 6)
    assert end == date(2026, 7, 10)


def test_parse_report_range_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="on or before"):
        parse_report_range(_range_view(start="2026-07-10", end="2026-07-06"))


def test_parse_report_range_requires_dates() -> None:
    with pytest.raises(ValueError, match="start date is required"):
        parse_report_range(_range_view(start=None))
    with pytest.raises(ValueError, match="end date is required"):
        parse_report_range(_range_view(end=None))


def test_parse_report_range_rejects_bad_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        parse_report_range(_range_view(metadata="not-json"))


def test_build_view_prefills_dates_and_metadata() -> None:
    view = report_range.build_view(
        channel_id="C1", user_id="U1", start=date(2026, 7, 6), end=date(2026, 7, 10)
    )
    assert view["callback_id"] == report_range.CALLBACK_ID
    assert json.loads(view["private_metadata"]) == {"channel_id": "C1", "user_id": "U1"}
    pickers = {
        b["block_id"]: b["element"]["initial_date"] for b in view["blocks"] if b["type"] == "input"
    }
    assert pickers[report_range.BLOCK_START] == "2026-07-06"
    assert pickers[report_range.BLOCK_END] == "2026-07-10"


@time_machine.travel("2026-07-09 15:00:00")  # a Thursday
def test_default_report_range_is_monday_to_today() -> None:
    start, end = _default_report_range("UTC")
    assert start == date(2026, 7, 6)  # Monday of that week
    assert end == date(2026, 7, 9)


@time_machine.travel("2026-07-09 15:00:00")
def test_default_report_range_falls_back_on_bad_tz() -> None:
    start, end = _default_report_range("Not/AZone")
    assert start == date(2026, 7, 6)
    assert end == date(2026, 7, 9)
