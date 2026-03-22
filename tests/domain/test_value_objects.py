from prbot.domain.value_objects import EmojiReaction, PRStatus, PRUrl


class TestPRUrl:
    def test_from_valid_url(self) -> None:
        pr = PRUrl.from_url("https://github.com/octocat/hello-world/pull/42")
        assert pr is not None
        assert pr.owner == "octocat"
        assert pr.repo == "hello-world"
        assert pr.number == 42

    def test_from_url_without_scheme(self) -> None:
        pr = PRUrl.from_url("github.com/octocat/repo/pull/1")
        assert pr is not None
        assert pr.number == 1

    def test_from_invalid_url_returns_none(self) -> None:
        assert PRUrl.from_url("https://example.com/not-a-pr") is None
        assert PRUrl.from_url("https://github.com/octocat/repo/issues/1") is None
        assert PRUrl.from_url("just some text") is None

    def test_from_url_embedded_in_text(self) -> None:
        text = "Check out https://github.com/org/repo/pull/99 please"
        pr = PRUrl.from_url(text)
        assert pr is not None
        assert pr.owner == "org"
        assert pr.repo == "repo"
        assert pr.number == 99

    def test_full_url(self) -> None:
        pr = PRUrl(owner="octocat", repo="repo", number=5)
        assert pr.full_url == "https://github.com/octocat/repo/pull/5"

    def test_is_frozen(self) -> None:
        pr = PRUrl(owner="a", repo="b", number=1)
        try:
            pr.owner = "c"  # type: ignore[misc]
            msg = "Should have raised"
            raise AssertionError(msg)
        except Exception:
            pass


class TestEmojiReaction:
    def test_from_status_mapping(self) -> None:
        assert EmojiReaction.from_status(PRStatus.MERGED) == EmojiReaction.MERGED
        assert EmojiReaction.from_status(PRStatus.CLOSED) == EmojiReaction.CLOSED
        assert (
            EmojiReaction.from_status(PRStatus.CHANGES_REQUESTED) == EmojiReaction.CHANGES_REQUESTED
        )
        assert EmojiReaction.from_status(PRStatus.APPROVED) == EmojiReaction.APPROVED
        assert EmojiReaction.from_status(PRStatus.COMMENTED) == EmojiReaction.COMMENTED
        assert EmojiReaction.from_status(PRStatus.OPEN) == EmojiReaction.OPEN

    def test_emoji_values(self) -> None:
        assert EmojiReaction.MERGED.value == "tada"
        assert EmojiReaction.CLOSED.value == "x"
        assert EmojiReaction.OPEN.value == "eyes"
