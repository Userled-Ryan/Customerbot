from __future__ import annotations

from customerbot.application.intake.ticket_card import (
    ACTION_ADD_AFFECTED_ORG,
    ACTION_MOVE_TO_DEV,
    ACTION_RECLASSIFY,
    ACTION_REOPEN,
    ACTION_RESOLVED,
    ACTION_RESOLVED_HOTFIX,
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
        ACTION_REOPEN,
    }


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


def test_fallback_text_is_compact() -> None:
    text = fallback_text(_ticket())
    assert "TIC-007" in text
    assert "P1" in text
    assert len(text) <= 200
