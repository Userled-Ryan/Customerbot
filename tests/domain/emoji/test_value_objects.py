from prbot.domain.emoji.value_objects import EmojiConfig


class TestEmojiConfig:
    def test_default_mapping(self) -> None:
        config = EmojiConfig()
        assert config.for_status("merged") == "git-merged"
        assert config.for_status("closed") == "headstone"
        assert config.for_status("changes_requested") == "git-changes-requested"
        assert config.for_status("approved") == "git-approved"
        assert config.for_status("commented") == "speech_balloon"

    def test_open_returns_none(self) -> None:
        config = EmojiConfig()
        assert config.for_status("open") is None

    def test_custom_emoji(self) -> None:
        config = EmojiConfig(merged="rocket", approved="shipit")
        assert config.for_status("merged") == "rocket"
        assert config.for_status("approved") == "shipit"
        assert config.for_status("closed") == "headstone"  # unchanged default

    def test_fallback_returns_unicode_for_known_statuses(self) -> None:
        for status in ("merged", "closed", "changes_requested", "approved", "commented"):
            fallback = EmojiConfig.fallback_for_status(status)
            assert fallback is not None
            assert not fallback.isascii(), f"fallback for {status} should be unicode"

    def test_fallback_returns_none_for_unknown_status(self) -> None:
        assert EmojiConfig.fallback_for_status("open") is None
