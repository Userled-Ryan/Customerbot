import asyncio
import logging

from prbot.application.handle_github_webhook import HandleGitHubWebhook
from prbot.domain.ports import PRRepositoryPort
from prbot.domain.value_objects import PRUrl

logger = logging.getLogger(__name__)


class ReconcileTrackedPRs:
    """Use case: on startup, re-check all tracked PRs to catch missed events."""

    def __init__(
        self,
        pr_repository: PRRepositoryPort,
        handle_webhook: HandleGitHubWebhook,
        concurrency: int = 5,
        batch_size: int = 20,
    ) -> None:
        self._repo = pr_repository
        self._handle_webhook = handle_webhook
        self._concurrency = concurrency
        self._batch_size = batch_size

    async def execute(self) -> None:
        pr_urls = await self._repo.find_distinct_pr_urls()
        if not pr_urls:
            logger.info("Reconciliation: no tracked PRs found, nothing to do")
            return

        logger.info("Reconciliation: checking %d tracked PRs", len(pr_urls))

        succeeded = 0
        failed = 0
        semaphore = asyncio.Semaphore(self._concurrency)

        for i in range(0, len(pr_urls), self._batch_size):
            batch = pr_urls[i : i + self._batch_size]
            results = await asyncio.gather(
                *(self._reconcile_one(semaphore, pr_url) for pr_url in batch),
            )
            succeeded += results.count(True)
            failed += results.count(False)
        logger.info(
            "Reconciliation complete: %d succeeded, %d failed out of %d PRs",
            succeeded,
            failed,
            len(pr_urls),
        )

    async def _reconcile_one(self, semaphore: asyncio.Semaphore, pr_url: PRUrl) -> bool:
        async with semaphore:
            logger.debug("Reconciling %s", pr_url)
            try:
                await self._handle_webhook.execute(
                    owner=pr_url.owner,
                    repo=pr_url.repo,
                    number=pr_url.number,
                )
            except Exception:
                logger.warning("Reconciliation failed for %s", pr_url, exc_info=True)
                return False
            return True
