import logging
import time

import httpx
import jwt

from prbot.domain.value_objects import PRInfo, PRUrl, Review, ReviewState

logger = logging.getLogger(__name__)


class GitHubGateway:
    """Fetches PR data from GitHub REST API using GitHub App installation tokens."""

    def __init__(self, app_id: str, private_key: str) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )
        # Cache: installation_id -> (token, expires_at)
        self._token_cache: dict[int, tuple[str, float]] = {}
        # Cache: owner -> installation_id
        self._installation_cache: dict[str, int] = {}

    def _generate_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def _get_installation_id(self, owner: str) -> int:
        if owner in self._installation_cache:
            return self._installation_cache[owner]

        token = self._generate_jwt()
        resp = await self._client.get(
            f"/orgs/{owner}/installation",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 404:
            # Try as a user installation
            resp = await self._client.get(
                f"/users/{owner}/installation",
                headers={"Authorization": f"Bearer {token}"},
            )
        resp.raise_for_status()
        installation_id: int = resp.json()["id"]
        self._installation_cache[owner] = installation_id
        return installation_id

    async def _get_token(self, owner: str) -> str:
        installation_id = await self._get_installation_id(owner)

        cached = self._token_cache.get(installation_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]

        jwt_token = self._generate_jwt()
        resp = await self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        token: str = data["token"]
        # Parse ISO 8601 expiry — tokens last 1 hour
        from datetime import datetime

        expires_at = datetime.fromisoformat(data["expires_at"]).timestamp()
        self._token_cache[installation_id] = (token, expires_at)
        logger.info("Obtained installation token for %s (installation %d)", owner, installation_id)
        return token

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        token = await self._get_token(pr_url.owner)
        headers = {"Authorization": f"Bearer {token}"}

        pr_resp = await self._client.get(
            f"/repos/{pr_url.owner}/{pr_url.repo}/pulls/{pr_url.number}",
            headers=headers,
        )
        pr_resp.raise_for_status()
        pr_data = pr_resp.json()

        reviews: list[Review] = []
        page = 1
        while True:
            rev_resp = await self._client.get(
                f"/repos/{pr_url.owner}/{pr_url.repo}/pulls/{pr_url.number}/reviews",
                params={"per_page": 100, "page": page},
                headers=headers,
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
