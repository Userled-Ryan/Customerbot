import pytest

from prbot.application.handle_github_webhook import HandleGitHubWebhook
from prbot.config import EmojiConfig
from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import PRInfo, PRUrl, Review, ReviewState
from tests.conftest import FakeGitHubClient, FakePRRepository, FakeSlackReactions


def _pr_url() -> PRUrl:
    return PRUrl(owner="o", repo="r", number=1)


EMOJI = EmojiConfig()


class TestHandleGitHubWebhook:
    @pytest.fixture
    def slack(self) -> FakeSlackReactions:
        return FakeSlackReactions()

    @pytest.fixture
    def repo(self) -> FakePRRepository:
        return FakePRRepository()

    async def test_webhook_adds_emoji_for_new_status(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(TrackedPR(pr_url=_pr_url(), channel_id="C123", message_ts="1234.5678"))
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        github = FakeGitHubClient(merged_info)
        use_case = HandleGitHubWebhook(github, slack, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(slack.added) == 1
        assert slack.added[0] == ("C123", "1234.5678", "tada")

    async def test_webhook_skips_already_applied_emoji(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                channel_id="C123",
                message_ts="1234.5678",
                applied_emojis=frozenset({"tada"}),
            )
        )
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        github = FakeGitHubClient(merged_info)
        use_case = HandleGitHubWebhook(github, slack, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(slack.added) == 0

    async def test_webhook_no_reaction_for_open_status(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(TrackedPR(pr_url=_pr_url(), channel_id="C123", message_ts="1234.5678"))
        open_info = PRInfo(state="open", merged=False, reviews=())
        github = FakeGitHubClient(open_info)
        use_case = HandleGitHubWebhook(github, slack, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(slack.added) == 0

    async def test_webhook_updates_all_tracked_messages(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        for i in range(3):
            repo.stored.append(
                TrackedPR(pr_url=_pr_url(), channel_id=f"C{i}", message_ts=f"{i}.0000")
            )
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        github = FakeGitHubClient(merged_info)
        use_case = HandleGitHubWebhook(github, slack, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(slack.added) == 3

    async def test_webhook_for_untracked_pr(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        github = FakeGitHubClient(merged_info)
        use_case = HandleGitHubWebhook(github, slack, repo, EMOJI)

        await use_case.execute("o", "r", 999)

        assert len(slack.added) == 0

    async def test_webhook_adds_approval_to_existing_emojis(
        self, slack: FakeSlackReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                channel_id="C123",
                message_ts="1234.5678",
                applied_emojis=frozenset({"speech_balloon"}),
            )
        )
        approved_info = PRInfo(
            state="open",
            merged=False,
            reviews=(Review(user_login="alice", state=ReviewState.APPROVED),),
        )
        github = FakeGitHubClient(approved_info)
        use_case = HandleGitHubWebhook(github, slack, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(slack.added) == 1
        assert slack.added[0][2] == "white_check_mark"
