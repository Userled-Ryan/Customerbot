from prbot.config import EmojiConfig
from prbot.domain.value_objects import PRStatus, PRUrl


class TestPRUrl:
    def test_is_frozen(self) -> None:
        pr = PRUrl(owner="a", repo="b", number=1)
        try:
            pr.owner = "c"  # type: ignore[misc]
            msg = "Should have raised"
            raise AssertionError(msg)
        except Exception:
            pass


class TestEmojiConfig:
    def test_default_mapping(self) -> None:
        config = EmojiConfig()
        assert config.for_status(PRStatus.MERGED) == "git-merged"
        assert config.for_status(PRStatus.CLOSED) == "tombstoene"
        assert config.for_status(PRStatus.CHANGES_REQUESTED) == "git-changes-requested"
        assert config.for_status(PRStatus.APPROVED) == "git-approved"
        assert config.for_status(PRStatus.COMMENTED) == "speech_balloon"

    def test_open_returns_none(self) -> None:
        config = EmojiConfig()
        assert config.for_status(PRStatus.OPEN) is None

    def test_custom_emoji(self) -> None:
        config = EmojiConfig(merged="rocket", approved="shipit")
        assert config.for_status(PRStatus.MERGED) == "rocket"
        assert config.for_status(PRStatus.APPROVED) == "shipit"
        assert config.for_status(PRStatus.CLOSED) == "tombstoene"  # unchanged default
