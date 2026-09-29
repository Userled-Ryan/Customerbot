"""Release-announcement parsing for the deploy hold.

Releases are announced in #engineering by whoever runs them, pasting the output
of the release script:

    These commits are about to be merged into release!
    --------------------------------------------------
    <@U…|Nic> 45aec5192 [PRO-1666]: Cut audience sweep allocations (#9790) (12 minutes ago)
    …

Replies in that thread may queue more commits (same line shape) and, once the
deploy workflow is kicked off, carry the GitHub Actions run link
(`<https://github.com/userledio/core/actions/runs/36429619765|…>`). That link is
the "deployed" signal: a merged-but-unreleased PR has not reached customers yet,
so the customer-facing "resolved" reply waits for it.

Only the `(#NNNN)` PR number is reliable on a commit line — the `[PRO-…]` Linear
id is often missing and its casing varies — so matching is by PR number within
the one watched repo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

RELEASE_MARKER = "These commits are about to be merged into release"

# `(#9790)` — the squash-merge PR suffix on each commit line.
_PR_NUMBER_RE = re.compile(r"\(#(\d+)\)")


def is_release_announcement(text: str) -> bool:
    return RELEASE_MARKER.lower() in text.lower()


def parse_pr_numbers(text: str) -> set[int]:
    """Every `(#NNNN)` PR number in a release post or a queued-commits reply."""
    return {int(n) for n in _PR_NUMBER_RE.findall(text)}


def parse_run_url(text: str, repo: str) -> str | None:
    """The first GitHub Actions run URL for `repo` (`owner/name`) in `text`.

    Works on raw Slack mrkdwn, where the link arrives as `<url|label>`.
    """
    match = re.search(
        rf"https://github\.com/{re.escape(repo)}/actions/runs/\d+", text, re.IGNORECASE
    )
    return match.group(0) if match else None


def pr_number_from_url(url: str, repo: str) -> int | None:
    """PR number from a `https://github.com/{repo}/pull/N` URL, or None when the
    URL isn't a PR in `repo` (a PR elsewhere is never announced, so never held)."""
    match = re.search(rf"https?://github\.com/{re.escape(repo)}/pull/(\d+)", url, re.IGNORECASE)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class Release:
    """A release thread seen in #engineering, keyed by its parent message ts."""

    release_ts: str
    channel_id: str
    run_url: str | None
    deployed_at: datetime | None


@dataclass(frozen=True)
class DeployHold:
    """A resolved ticket's customer reply waiting on one PR to be deployed."""

    ticket_id: int
    pr_number: int
    pr_url: str
    created_at: datetime
    deployed_at: datetime | None
    nudged_at: datetime | None
