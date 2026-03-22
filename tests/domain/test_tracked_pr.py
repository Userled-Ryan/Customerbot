from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import EmojiReaction, PRUrl


def _make_tracked(emoji: EmojiReaction | None = None) -> TrackedPR:
    return TrackedPR(
        pr_url=PRUrl(owner="o", repo="r", number=1),
        channel_id="C123",
        message_ts="1234.5678",
        current_emoji=emoji,
    )


class TestTrackedPR:
    def test_needs_update_returns_true_for_different_emoji(self) -> None:
        tracked = _make_tracked(EmojiReaction.OPEN)
        assert tracked.needs_update(EmojiReaction.APPROVED) is True

    def test_needs_update_returns_false_for_same_emoji(self) -> None:
        tracked = _make_tracked(EmojiReaction.OPEN)
        assert tracked.needs_update(EmojiReaction.OPEN) is False

    def test_needs_update_returns_true_when_no_current_emoji(self) -> None:
        tracked = _make_tracked(None)
        assert tracked.needs_update(EmojiReaction.OPEN) is True

    def test_with_emoji_returns_new_instance(self) -> None:
        tracked = _make_tracked(EmojiReaction.OPEN)
        updated = tracked.with_emoji(EmojiReaction.MERGED)
        assert updated.current_emoji == EmojiReaction.MERGED
        assert tracked.current_emoji == EmojiReaction.OPEN  # original unchanged
