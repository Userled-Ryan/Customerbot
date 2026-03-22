import httpx

from prbot.domain.value_objects import PRInfo, PRUrl, Review, ReviewState


class GitHubGateway:
    """Concrete adapter: fetches PR data from GitHub REST API using httpx."""

    def __init__(self, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        pr_resp = await self._client.get(
            f"/repos/{pr_url.owner}/{pr_url.repo}/pulls/{pr_url.number}"
        )
        pr_resp.raise_for_status()
        pr_data = pr_resp.json()

        reviews: list[Review] = []
        page = 1
        while True:
            rev_resp = await self._client.get(
                f"/repos/{pr_url.owner}/{pr_url.repo}/pulls/{pr_url.number}/reviews",
                params={"per_page": 100, "page": page},
            )
            rev_resp.raise_for_status()
            page_data = rev_resp.json()
            if not page_data:
                break
            for r in page_data:
                reviews.append(
                    Review(
                        user_login=r["user"]["login"],
                        state=ReviewState(r["state"]),
                    )
                )
            if len(page_data) < 100:
                break
            page += 1

        return PRInfo(
            state=pr_data["state"],
            merged=pr_data.get("merged", False),
            reviews=tuple(reviews),
        )

    async def close(self) -> None:
        await self._client.aclose()
