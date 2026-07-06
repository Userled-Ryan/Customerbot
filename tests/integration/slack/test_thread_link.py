"""parse_thread_link — the inverse of build_thread_link used for reactions."""

from __future__ import annotations

import pytest

from customerbot.integration.slack.gateway import build_thread_link, parse_thread_link


@pytest.mark.parametrize(
    ("channel_id", "thread_ts"),
    [
        ("C0ARYPD3E5A", "1720280400.123456"),
        ("C123", "1700000000.000100"),
    ],
)
def test_round_trip(channel_id: str, thread_ts: str) -> None:
    link = build_thread_link("https://userled.slack.com", channel_id, thread_ts)
    assert parse_thread_link(link) == (channel_id, thread_ts)


@pytest.mark.parametrize(
    "link",
    [
        "",
        "not a link",
        "https://userled.slack.com/team/U123",  # no /archives/
        "https://userled.slack.com/archives/C123",  # no ts segment
        "https://userled.slack.com/archives/C123/pabc",  # non-numeric ts
        "https://userled.slack.com/archives//p1720280400123456",  # empty channel
    ],
)
def test_returns_none_for_non_thread_links(link: str) -> None:
    assert parse_thread_link(link) is None
