import pytest
import respx
from httpx import Response

from prbot.domain.value_objects import PRUrl, ReviewState
from prbot.infrastructure.github_gateway import GitHubGateway


@pytest.fixture
def pr_url() -> PRUrl:
    return PRUrl(owner="octocat", repo="hello", number=1)


@pytest.fixture
def gateway() -> GitHubGateway:
    return GitHubGateway(token="fake-token")


class TestGitHubGateway:
    @respx.mock
    async def test_fetch_open_pr_no_reviews(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(200, json={"state": "open", "merged": False})
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(200, json=[])
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.state == "open"
        assert info.merged is False
        assert info.reviews == ()

    @respx.mock
    async def test_fetch_merged_pr(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(200, json={"state": "closed", "merged": True})
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(200, json=[])
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert info.state == "closed"
        assert info.merged is True

    @respx.mock
    async def test_fetch_pr_with_reviews(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(200, json={"state": "open", "merged": False})
        )
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1/reviews").mock(
            return_value=Response(
                200,
                json=[
                    {"user": {"login": "alice"}, "state": "APPROVED"},
                    {"user": {"login": "bob"}, "state": "CHANGES_REQUESTED"},
                ],
            )
        )

        info = await gateway.fetch_pr_info(pr_url)

        assert len(info.reviews) == 2
        assert info.reviews[0].user_login == "alice"
        assert info.reviews[0].state == ReviewState.APPROVED
        assert info.reviews[1].user_login == "bob"
        assert info.reviews[1].state == ReviewState.CHANGES_REQUESTED

    @respx.mock
    async def test_handles_api_error(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(Exception):  # noqa: B017
            await gateway.fetch_pr_info(pr_url)
