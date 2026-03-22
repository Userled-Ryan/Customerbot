import pytest

from prbot.application.handle_slack_message import HandleSlackMessage
from prbot.config import EmojiConfig
from prbot.domain.value_objects import PRInfo, Review, ReviewState
from tests.conftest import FakeGitHubClient, FakePRRepository, FakeSlackReactions


def _open_pr() -> PRInfo:
    return PRInfo(state="open", merged=False, reviews=())


def _approved_pr() -> PRInfo:
    return PRInfo(
        state="open",
        merged=False,
        reviews=(Review(user_login="alice", state=ReviewState.APPROVED),),
    )


EMOJI = EmojiConfig()


class TestHandleSlackMessage:
    @pytest.fixture
    def slack(self) -> FakeSlackReactions:
        return FakeSlackReactions()

    @pytest.fixture
    def repo(self) -> FakePRRepository:
        return FakePRRepository()

    async def test_open_pr_gets_no_reaction(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        github = FakeGitHubClient(_open_pr())
        use_case = HandleSlackMessage(github, slack, repo, EMOJI)

        await use_case.execute("C123", "1234.5678", "Check https://github.com/o/r/pull/1")

        assert len(slack.added) == 0
        assert len(repo.stored) == 1
        assert repo.stored[0].applied_emojis == frozenset()

    async def test_message_without_pr_url_does_nothing(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        github = FakeGitHubClient(_open_pr())
        use_case = HandleSlackMessage(github, slack, repo, EMOJI)

        await use_case.execute("C123", "1234.5678", "Just a normal message")

        assert len(slack.added) == 0
        assert len(repo.stored) == 0

    async def test_message_with_multiple_pr_urls(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        github = FakeGitHubClient(_approved_pr())
        use_case = HandleSlackMessage(github, slack, repo, EMOJI)

        text = "See github.com/o/r/pull/1 and github.com/o/r/pull/2"
        await use_case.execute("C123", "1234.5678", text)

        assert len(slack.added) == 2
        assert len(repo.stored) == 2

    async def test_duplicate_pr_url_only_processed_once(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        github = FakeGitHubClient(_approved_pr())
        use_case = HandleSlackMessage(github, slack, repo, EMOJI)

        text = "github.com/o/r/pull/1 and github.com/o/r/pull/1 again"
        await use_case.execute("C123", "1234.5678", text)

        assert len(slack.added) == 1

    async def test_approved_pr_gets_correct_emoji(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        github = FakeGitHubClient(_approved_pr())
        use_case = HandleSlackMessage(github, slack, repo, EMOJI)

        await use_case.execute("C123", "1234.5678", "github.com/o/r/pull/1")

        assert slack.added[0] == ("C123", "1234.5678", "white_check_mark")

    async def test_pr_stored_in_repository(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        github = FakeGitHubClient(_approved_pr())
        use_case = HandleSlackMessage(github, slack, repo, EMOJI)

        await use_case.execute("C123", "1234.5678", "github.com/o/r/pull/1")

        assert len(repo.stored) == 1
        tracked = repo.stored[0]
        assert tracked.pr_url.owner == "o"
        assert tracked.pr_url.repo == "r"
        assert tracked.pr_url.number == 1
        assert tracked.channel_id == "C123"
        assert tracked.message_ts == "1234.5678"
        assert tracked.applied_emojis == frozenset({"white_check_mark"})

    async def test_custom_emoji_config(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        github = FakeGitHubClient(_approved_pr())
        custom = EmojiConfig(approved="shipit")
        use_case = HandleSlackMessage(github, slack, repo, custom)

        await use_case.execute("C123", "1234.5678", "github.com/o/r/pull/1")

        assert slack.added[0][2] == "shipit"
