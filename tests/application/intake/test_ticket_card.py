from __future__ import annotations

from customerbot.application.intake.ticket_card import (
    ACTION_ADD_AFFECTED_ORG,
    ACTION_DROP,
    ACTION_MOVE_TO_DEV,
    ACTION_RECLASSIFY,
    ACTION_REOPEN,
    ACTION_RESOLVED,
    ACTION_RESOLVED_HOTFIX,
    ACTION_TOGGLE_REPLY_NEEDED,
    build_blocks,
    fallback_text,
)
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _ticket(**overrides: object) -> Ticket:
    base: dict[str, object] = {
        "id": 7,
        "title": "Publishing fails on Safari",
        "type": TicketType.BUG,
        "subtype": TicketSubtype.PLATFORM_WIDE,
        "severity": Severity.BLOCKING,
        "priority": Priority.P1,
        "status": TicketStatus.NEW,
        "lane": Lane.SE_ACTION,
        "reporter_user_id": "U_SE",
        "source": Source.CUSTOMER_CHANNEL,
        "description": "Repro on iOS Safari 17.3",
        "original_slack_link": "https://x.slack.com/p123",
    }
    base.update(overrides)
    return Ticket(**base)  # type: ignore[arg-type]


def test_card_contains_six_action_buttons() -> None:
    blocks = build_blocks(_ticket(), ["Acme"])
    action_block = next(b for b in blocks if b["type"] == "actions")
    action_ids = {el["action_id"] for el in action_block["elements"]}
    assert action_ids == {
        ACTION_RESOLVED,
        ACTION_RESOLVED_HOTFIX,
        ACTION_MOVE_TO_DEV,
        ACTION_RECLASSIFY,
        ACTION_ADD_AFFECTED_ORG,
        ACTION_DROP,
    }


def test_live_card_has_no_reopen_button() -> None:
    # Reopen only makes sense on a closed ticket; it no-ops on a live one, so
    # it's deliberately absent from the live button set (replaced by Drop).
    blocks = build_blocks(_ticket(status=TicketStatus.NEW), [])
    action_ids = {el["action_id"] for b in blocks if b["type"] == "actions" for el in b["elements"]}
    assert ACTION_REOPEN not in action_ids


def test_drop_button_carries_confirmation_dialog() -> None:
    blocks = build_blocks(_ticket(), [])
    drop = next(
        el
        for b in blocks
        if b["type"] == "actions"
        for el in b["elements"]
        if el["action_id"] == ACTION_DROP
    )
    assert drop["style"] == "danger"
    assert "confirm" in drop  # native Slack "are you sure?" before it fires


def test_closed_card_collapses_to_reopen_only() -> None:
    blocks = build_blocks(_ticket(status=TicketStatus.CLOSED), ["Acme"])
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    action_ids = {el["action_id"] for b in action_blocks for el in b["elements"]}
    assert action_ids == {ACTION_REOPEN}


def test_closed_card_header_is_struck_through_and_locked() -> None:
    blocks = build_blocks(_ticket(title="Foo", status=TicketStatus.CLOSED), [])
    header = blocks[0]["text"]["text"]
    assert ":lock:" in header
    assert "~Foo~" in header


def test_awaiting_card_header_shows_check() -> None:
    blocks = build_blocks(_ticket(status=TicketStatus.AWAITING_CUSTOMER), [])
    header = blocks[0]["text"]["text"]
    assert ":white_check_mark:" in header


def _toggle_button(blocks: list[dict]) -> dict | None:
    for b in blocks:
        if b["type"] != "actions":
            continue
        for el in b["elements"]:
            if el["action_id"] == ACTION_TOGGLE_REPLY_NEEDED:
                return el
    return None


def _rendered_text(blocks: list[dict]) -> str:
    parts: list[str] = []

    def _add(text: object) -> None:
        # section text is a dict {"type","text"}; context element text is a str.
        if isinstance(text, dict):
            parts.append(str(text.get("text", "")))
        elif isinstance(text, str):
            parts.append(text)

    for b in blocks:
        if "text" in b:
            _add(b["text"])
        for el in b.get("elements", []):
            if isinstance(el, dict) and "text" in el:
                _add(el["text"])
    return "\n".join(parts)


def test_live_card_has_reply_needed_toggle_labelled_for_state() -> None:
    off = _toggle_button(build_blocks(_ticket(reply_needed=False), []))
    on = _toggle_button(build_blocks(_ticket(reply_needed=True), []))
    assert off is not None and off["text"]["text"] == "Reply needed"
    assert on is not None and on["text"]["text"] == "Clear reply-needed"


def test_reply_needed_badge_only_when_flagged() -> None:
    # Badge string is distinct from the toggle button's "Reply needed" label.
    assert ":speech_balloon: *Reply needed*" not in _rendered_text(
        build_blocks(_ticket(reply_needed=False), [])
    )
    assert ":speech_balloon: *Reply needed*" in _rendered_text(
        build_blocks(_ticket(reply_needed=True), [])
    )


def test_closed_card_has_no_reply_needed_toggle_or_badge() -> None:
    # Closed cards collapse to Reopen only — the flag is meaningless once retired.
    blocks = build_blocks(_ticket(status=TicketStatus.CLOSED, reply_needed=True), [])
    assert _toggle_button(blocks) is None
    assert ":speech_balloon: *Reply needed*" not in _rendered_text(blocks)


def test_button_value_carries_ticket_id() -> None:
    blocks = build_blocks(_ticket(id=42), [])
    action_block = next(b for b in blocks if b["type"] == "actions")
    for el in action_block["elements"]:
        assert el["value"] == "42"


def test_card_header_shows_display_id_and_title() -> None:
    blocks = build_blocks(_ticket(id=3, title="Foo"), [])
    header = blocks[0]["text"]["text"]
    assert "TIC-003" in header
    assert "Foo" in header


def test_card_renders_no_orgs_placeholder_when_empty() -> None:
    blocks = build_blocks(_ticket(), [])
    fields_block = next(b for b in blocks if b.get("type") == "section" and "fields" in b)
    affected_field = next(f for f in fields_block["fields"] if "Affected orgs" in f["text"])
    assert "no orgs linked" in affected_field["text"]


def test_card_omits_description_block_when_blank() -> None:
    blocks = build_blocks(_ticket(description=""), [])
    section_texts = [b.get("text", {}).get("text", "") for b in blocks if b["type"] == "section"]
    assert not any("Repro on iOS" in t for t in section_texts)


def test_card_renders_csm_stakeholders() -> None:
    blocks = build_blocks(_ticket(), ["Acme"], ["U_CSM1", "U_CSM2"])
    fields_block = next(b for b in blocks if b.get("type") == "section" and "fields" in b)
    stakeholders = next(f for f in fields_block["fields"] if "Stakeholders" in f["text"])
    assert "<@U_CSM1>" in stakeholders["text"]
    assert "<@U_CSM2>" in stakeholders["text"]


def test_card_dedupes_stakeholders_and_handles_none() -> None:
    # One CSM owning two affected orgs shouldn't be mentioned twice.
    deduped = build_blocks(_ticket(), ["A", "B"], ["U_CSM", "U_CSM"])
    fields = next(b for b in deduped if b.get("type") == "section" and "fields" in b)["fields"]
    stakeholders = next(f for f in fields if "Stakeholders" in f["text"])
    assert stakeholders["text"].count("<@U_CSM>") == 1

    # No CSMs → placeholder, not a crash.
    none_blocks = build_blocks(_ticket(), ["A"])
    fields = next(b for b in none_blocks if b.get("type") == "section" and "fields" in b)["fields"]
    stakeholders = next(f for f in fields if "Stakeholders" in f["text"])
    assert stakeholders["text"] == "*Stakeholders*\n—"


def test_fallback_text_is_compact() -> None:
    text = fallback_text(_ticket())
    assert "TIC-007" in text
    assert "P1" in text
    assert len(text) <= 200
