from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import PRUrl


def _make_tracked(emojis: frozenset[str] = frozenset()) -> TrackedPR:
    return TrackedPR(
        pr_url=PRUrl(owner="o", repo="r", number=1),
        channel_id="C123",
        message_ts="1234.5678",
        applied_emojis=emojis,
    )


class TestTrackedPR:
    def test_has_emoji_returns_true_when_present(self) -> None:
        tracked = _make_tracked(frozenset({"eyes"}))
        assert tracked.has_emoji("eyes") is True

    def test_has_emoji_returns_false_when_absent(self) -> None:
        tracked = _make_tracked(frozenset({"eyes"}))
        assert tracked.has_emoji("tada") is False

    def test_has_emoji_returns_false_when_empty(self) -> None:
        tracked = _make_tracked()
        assert tracked.has_emoji("eyes") is False

    def test_with_added_emoji_returns_new_instance(self) -> None:
        tracked = _make_tracked(frozenset({"eyes"}))
        updated = tracked.with_added_emoji("tada")
        assert updated.applied_emojis == frozenset({"eyes", "tada"})
        assert tracked.applied_emojis == frozenset({"eyes"})  # original unchanged

    def test_with_added_emoji_is_idempotent(self) -> None:
        tracked = _make_tracked(frozenset({"eyes"}))
        updated = tracked.with_added_emoji("eyes")
        assert updated.applied_emojis == frozenset({"eyes"})
