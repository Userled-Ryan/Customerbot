"""Pure tests for the dedupe scoring helper + the stash payload codec."""

from __future__ import annotations

from customerbot.application.intake.dedupe import StashedTicketPayload, token_overlap


def test_token_overlap_identical_is_one() -> None:
    assert token_overlap("hello world", "hello world") == 1.0


def test_token_overlap_disjoint_is_zero() -> None:
    assert token_overlap("hello world", "foo bar") == 0.0


def test_token_overlap_is_case_insensitive() -> None:
    assert token_overlap("Hello World", "hello WORLD") == 1.0


def test_token_overlap_jaccard_math() -> None:
    # tokens A = {a, b, c}; tokens B = {b, c, d, e}; overlap = 2/5
    assert token_overlap("a b c", "b c d e") == 2 / 5


def test_token_overlap_empty_inputs_are_zero() -> None:
    assert token_overlap("", "anything") == 0.0
    assert token_overlap("anything", "") == 0.0
    assert token_overlap("", "") == 0.0


def test_token_overlap_strips_punctuation() -> None:
    """Word boundaries — punctuation shouldn't fragment matches."""
    assert token_overlap("hello, world!", "hello world") == 1.0


def test_stashed_payload_round_trip() -> None:
    p = StashedTicketPayload(
        kind="se_bug",
        ticket_dump={"title": "x", "type": "bug"},
        org_id="acme",
        reporter_user_id="U_SE",
        slack_view_id="V_1",
        original_slack_link="https://x/p1",
    )
    restored = StashedTicketPayload.from_json(p.to_json())
    assert restored == p


def test_stashed_payload_round_trip_with_nulls() -> None:
    p = StashedTicketPayload(
        kind="csm_intake",
        ticket_dump={},
        org_id="acme",
        reporter_user_id="U_CSM",
        slack_view_id=None,
        original_slack_link=None,
    )
    restored = StashedTicketPayload.from_json(p.to_json())
    assert restored.slack_view_id is None
    assert restored.original_slack_link is None
