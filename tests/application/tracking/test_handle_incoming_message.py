import pytest

from prbot.application.tracking.handle_incoming_message import HandleIncomingMessage
from prbot.domain.emoji.value_objects import EmojiConfig
from prbot.domain.tracking.value_objects import MessageRef, PRInfo, Review, ReviewState
from tests.conftest import FakeEmojiConfigResolver, FakePRRepository, FakePRSource, FakeReactions


def _msg_ref() -> MessageRef:
    return MessageRef(integration_id="slack", ref="C123:1234.5678")


def _open_pr() -> PRInfo:
    return PRInfo(state="open", merged=False, reviews=())


def _approved_pr() -> PRInfo:
    return PRInfo(
        state="open",
        merged=False,
        reviews=(Review(user_login="alice", state=ReviewState.APPROVED),),
    )


class TestHandleIncomingMessage:
    @pytest.fixture
    def reactions(self) -> FakeReactions:
        return FakeReactions()

    @pytest.fixture
    def repo(self) -> FakePRRepository:
        return FakePRRepository()

    @pytest.fixture
    def resolver(self) -> FakeEmojiConfigResolver:
        return FakeEmojiConfigResolver()

    async def test_open_pr_gets_no_reaction(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        source = FakePRSource(_open_pr())
        use_case = HandleIncomingMessage([source], reactions, repo, resolver)

        await use_case.execute(_msg_ref(), "Check https://github.com/o/r/pull/1")

        assert len(reactions.added) == 0
        assert len(repo.stored) == 1
        assert repo.stored[0].applied_emojis == frozenset()

    async def test_message_without_pr_url_does_nothing(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        source = FakePRSource(_open_pr())
        use_case = HandleIncomingMessage([source], reactions, repo, resolver)

        await use_case.execute(_msg_ref(), "Just a normal message")

        assert len(reactions.added) == 0
        assert len(repo.stored) == 0

    async def test_message_with_multiple_pr_urls(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        source = FakePRSource(_approved_pr())
        use_case = HandleIncomingMessage([source], reactions, repo, resolver)

        text = "See github.com/o/r/pull/1 and github.com/o/r/pull/2"
        await use_case.execute(_msg_ref(), text)

        assert len(reactions.added) == 2
        assert len(repo.stored) == 2

    async def test_duplicate_pr_url_only_processed_once(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        source = FakePRSource(_approved_pr())
        use_case = HandleIncomingMessage([source], reactions, repo, resolver)

        text = "github.com/o/r/pull/1 and github.com/o/r/pull/1 again"
        await use_case.execute(_msg_ref(), text)

        assert len(reactions.added) == 1

    async def test_approved_pr_gets_correct_emoji(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        source = FakePRSource(_approved_pr())
        use_case = HandleIncomingMessage([source], reactions, repo, resolver)

        await use_case.execute(_msg_ref(), "github.com/o/r/pull/1")

        assert reactions.added[0] == (_msg_ref(), "git-approved")

    async def test_pr_stored_in_repository(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        source = FakePRSource(_approved_pr())
        use_case = HandleIncomingMessage([source], reactions, repo, resolver)

        await use_case.execute(_msg_ref(), "github.com/o/r/pull/1")

        assert len(repo.stored) == 1
        tracked = repo.stored[0]
        assert tracked.pr_url.owner == "o"
        assert tracked.pr_url.repo == "r"
        assert tracked.pr_url.number == 1
        assert tracked.message_ref == _msg_ref()
        assert tracked.applied_emojis == frozenset({"git-approved"})

    async def test_custom_emoji_config(
        self, reactions: FakeReactions, repo: FakePRRepository
    ) -> None:
        source = FakePRSource(_approved_pr())
        custom_resolver = FakeEmojiConfigResolver(EmojiConfig(approved="shipit"))
        use_case = HandleIncomingMessage([source], reactions, repo, custom_resolver)

        await use_case.execute(_msg_ref(), "github.com/o/r/pull/1")

        assert reactions.added[0][1] == "shipit"
