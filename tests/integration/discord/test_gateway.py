from prbot.domain.tracking.value_objects import MessageRef
from prbot.integration.discord.gateway import INTEGRATION_ID, decode_ref, encode_ref


class TestEncodeDecodeRef:
    def test_encode_ref(self) -> None:
        ref = encode_ref("123456", "789012")
        assert ref == MessageRef(integration_id=INTEGRATION_ID, ref="123456:789012")

    def test_decode_ref(self) -> None:
        ref = MessageRef(integration_id=INTEGRATION_ID, ref="123456:789012")
        channel_id, message_id = decode_ref(ref)
        assert channel_id == 123456
        assert message_id == 789012

    def test_roundtrip(self) -> None:
        original_channel = "111222333"
        original_message = "444555666"
        ref = encode_ref(original_channel, original_message)
        channel_id, message_id = decode_ref(ref)
        assert channel_id == int(original_channel)
        assert message_id == int(original_message)
