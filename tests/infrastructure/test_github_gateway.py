import time

import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response
from prbot.domain.tracking.value_objects import PRUrl, ReviewState
from prbot.infrastructure.github_gateway import GitHubGateway

_test_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PRIVATE_KEY = _test_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


@pytest.fixture
def pr_url() -> PRUrl:
    return PRUrl(owner="octocat", repo="hello", number=1)


@pytest.fixture
def gateway() -> GitHubGateway:
    return GitHubGateway(app_id="12345", private_key=TEST_PRIVATE_KEY)


def _mock_installation_and_token() -> None:
    """Set up mocks for the GitHub App auth flow."""
    respx.get("https://api.github.com/orgs/octocat/installation").mock(
        return_value=Response(200, json={"id": 99})
    )
    respx.post("https://api.github.com/app/installations/99/access_tokens").mock(
        return_value=Response(
            201,
            json={
                "token": "ghs_fake_token",
                "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
            },
        )
    )


class TestExtractPrReferences:
    def test_extract_from_valid_url(self, gateway: GitHubGateway) -> None:
        refs = gateway.extract_pr_references("https://github.com/octocat/hello-world/pull/42")
        assert len(refs) == 1
        assert refs[0].owner == "octocat"
        assert refs[0].repo == "hello-world"
        assert refs[0].number == 42

    def test_extract_without_scheme(self, gateway: GitHubGateway) -> None:
        refs = gateway.extract_pr_references("github.com/octocat/repo/pull/1")
        assert len(refs) == 1
        assert refs[0].number == 1

    def test_no_match_returns_empty(self, gateway: GitHubGateway) -> None:
        assert gateway.extract_pr_references("https://example.com/not-a-pr") == []
        assert gateway.extract_pr_references("https://github.com/octocat/repo/issues/1") == []
        assert gateway.extract_pr_references("just some text") == []

    def test_extract_embedded_in_text(self, gateway: GitHubGateway) -> None:
        text = "Check out https://github.com/org/repo/pull/99 please"
        refs = gateway.extract_pr_references(text)
        assert len(refs) == 1
        assert refs[0].owner == "org"
        assert refs[0].number == 99

    def test_extract_deduplicates(self, gateway: GitHubGateway) -> None:
        text = "github.com/o/r/pull/1 and github.com/o/r/pull/1 again"
        refs = gateway.extract_pr_references(text)
        assert len(refs) == 1

    def test_extract_multiple(self, gateway: GitHubGateway) -> None:
        text = "github.com/o/r/pull/1 and github.com/o/r/pull/2"
        refs = gateway.extract_pr_references(text)
        assert len(refs) == 2


class TestGitHubGateway:
    @respx.mock
    async def test_fetch_open_pr_no_reviews(self, gateway: GitHubGateway, pr_url: PRUrl) -> None:
        _mock_installation_and_token()
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
        _mock_installation_and_token()
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
        _mock_installation_and_token()
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
        _mock_installation_and_token()
        respx.get("https://api.github.com/repos/octocat/hello/pulls/1").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(Exception):  # noqa: B017
            await gateway.fetch_pr_info(pr_url)
