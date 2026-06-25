"""Unit tests for ticket source-link helpers."""

from __future__ import annotations

from customerbot.application.tracking.links import linked_display_id, ticket_source_link
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import Source, TicketSubtype, TicketType


def _ticket(**overrides: object) -> Ticket:
    base: dict[str, object] = {
        "id": 42,
        "title": "Boom",
        "type": TicketType.BUG,
        "subtype": TicketSubtype.PLATFORM_WIDE,
        "reporter_user_id": "U_SE",
        "source": Source.CUSTOMER_CHANNEL,
    }
    base.update(overrides)
    return Ticket(**base)  # type: ignore[arg-type]


def test_link_prefers_card_when_present() -> None:
    t = _ticket(
        card_channel_id="C_CARDS",
        card_message_ts="1700000000.000100",
        original_slack_link="https://x.slack.com/archives/C_THREAD/p1",
    )
    link = ticket_source_link(t, "https://acme.slack.com")
    assert link == "https://acme.slack.com/archives/C_CARDS/p1700000000000100"


def test_link_falls_back_to_original_thread_without_card() -> None:
    t = _ticket(original_slack_link="https://x.slack.com/archives/C_THREAD/p1")
    assert (
        ticket_source_link(t, "https://acme.slack.com")
        == "https://x.slack.com/archives/C_THREAD/p1"
    )


def test_link_is_none_when_no_anchor() -> None:
    assert ticket_source_link(_ticket(), "https://acme.slack.com") is None


def test_linked_display_id_wraps_when_link_exists() -> None:
    t = _ticket(card_channel_id="C_CARDS", card_message_ts="1700000000.000100")
    assert linked_display_id(t, "https://acme.slack.com").startswith("<https://acme.slack.com/")
    assert "|TIC-042>" in linked_display_id(t, "https://acme.slack.com")


def test_linked_display_id_plain_when_no_link() -> None:
    assert linked_display_id(_ticket(), "https://acme.slack.com") == "TIC-042"
