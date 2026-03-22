import pytest

from prbot.application.handle_github_webhook import HandleGitHubWebhook
from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import EmojiReaction, PRInfo, PRUrl, Review, ReviewState
from tests.conftest import FakeGitHubClient, FakePRRepository, FakeSlackReactions


def _pr_url() -> PRUrl:
    return PRUrl(owner="o", repo="r", number=1)


class TestHandleGitHubWebhook:
    @pytest.fixture
    def slack(self) -> FakeSlackReactions:
        return FakeSlackReactions()

    @pytest.fixture
    def repo(self) -> FakePRRepository:
        return FakePRRepository()

    async def test_webhook_updates_emoji(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        # PR was tracked as open, now it's merged
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                channel_id="C123",
                message_ts="1234.5678",
                current_emoji=EmojiReaction.OPEN,
            )
        )
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        github = FakeGitHubClient(merged_info)
        use_case = HandleGitHubWebhook(github, slack, repo)

        await use_case.execute("o", "r", 1)

        assert len(slack.removed) == 1
        assert slack.removed[0] == ("C123", "1234.5678", EmojiReaction.OPEN)
        assert len(slack.added) == 1
        assert slack.added[0] == ("C123", "1234.5678", EmojiReaction.MERGED)

    async def test_webhook_no_change_if_same_status(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                channel_id="C123",
                message_ts="1234.5678",
                current_emoji=EmojiReaction.OPEN,
            )
        )
        open_info = PRInfo(state="open", merged=False, reviews=())
        github = FakeGitHubClient(open_info)
        use_case = HandleGitHubWebhook(github, slack, repo)

        await use_case.execute("o", "r", 1)

        assert len(slack.removed) == 0
        assert len(slack.added) == 0

    async def test_webhook_updates_all_tracked_messages(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        # Same PR tracked in 3 different messages
        for i in range(3):
            repo.stored.append(
                TrackedPR(
                    pr_url=_pr_url(),
                    channel_id=f"C{i}",
                    message_ts=f"{i}.0000",
                    current_emoji=EmojiReaction.OPEN,
                )
            )
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        github = FakeGitHubClient(merged_info)
        use_case = HandleGitHubWebhook(github, slack, repo)

        await use_case.execute("o", "r", 1)

        assert len(slack.removed) == 3
        assert len(slack.added) == 3

    async def test_webhook_for_untracked_pr(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        github = FakeGitHubClient(merged_info)
        use_case = HandleGitHubWebhook(github, slack, repo)

        await use_case.execute("o", "r", 999)

        assert len(slack.removed) == 0
        assert len(slack.added) == 0

    async def test_webhook_with_no_prior_emoji(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                channel_id="C123",
                message_ts="1234.5678",
                current_emoji=None,
            )
        )
        approved_info = PRInfo(
            state="open",
            merged=False,
            reviews=(Review(user_login="alice", state=ReviewState.APPROVED),),
        )
        github = FakeGitHubClient(approved_info)
        use_case = HandleGitHubWebhook(github, slack, repo)

        await use_case.execute("o", "r", 1)

        # Should add without trying to remove
        assert len(slack.removed) == 0
        assert len(slack.added) == 1
        assert slack.added[0][2] == EmojiReaction.APPROVED
