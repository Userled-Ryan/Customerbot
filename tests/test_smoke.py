from customerbot.domain.tracking.value_objects import MessageRef


def test_message_ref_round_trips() -> None:
    ref = MessageRef(integration_id="slack", ref="C123:1234.5678")
    assert ref.integration_id == "slack"
    assert ref.ref == "C123:1234.5678"
