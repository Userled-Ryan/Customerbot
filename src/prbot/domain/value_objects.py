from __future__ import annotations

import re
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel


class PRStatus(StrEnum):
    """Resolved status of a PR, ordered by priority."""

    MERGED = "merged"
    CLOSED = "closed"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    COMMENTED = "commented"
    OPEN = "open"


class EmojiReaction(StrEnum):
    """Slack emoji names (without colons) mapped to PR statuses."""

    MERGED = "tada"
    CLOSED = "x"
    CHANGES_REQUESTED = "arrows_counterclockwise"
    APPROVED = "white_check_mark"
    COMMENTED = "speech_balloon"
    OPEN = "eyes"

    @classmethod
    def from_status(cls, status: PRStatus) -> EmojiReaction:
        mapping: dict[PRStatus, EmojiReaction] = {
            PRStatus.MERGED: cls.MERGED,
            PRStatus.CLOSED: cls.CLOSED,
            PRStatus.CHANGES_REQUESTED: cls.CHANGES_REQUESTED,
            PRStatus.APPROVED: cls.APPROVED,
            PRStatus.COMMENTED: cls.COMMENTED,
            PRStatus.OPEN: cls.OPEN,
        }
        return mapping[status]


class ReviewState(StrEnum):
    """GitHub review states as returned by the API."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    COMMENTED = "COMMENTED"
    PENDING = "PENDING"
    DISMISSED = "DISMISSED"


class PRUrl(BaseModel, frozen=True):
    """Parsed GitHub PR URL as a value object."""

    owner: str
    repo: str
    number: int

    _PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")

    @classmethod
    def from_url(cls, url: str) -> PRUrl | None:
        """Parse a GitHub PR URL. Returns None if not a valid PR URL."""
        match = cls._PATTERN.search(url)
        if not match:
            return None
        return cls(owner=match.group(1), repo=match.group(2), number=int(match.group(3)))

    @property
    def full_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def api_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/pulls/{self.number}"

    @property
    def reviews_api_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/pulls/{self.number}/reviews"


class Review(BaseModel, frozen=True):
    """A single review on a PR."""

    user_login: str
    state: ReviewState


class PRInfo(BaseModel, frozen=True):
    """Data fetched from GitHub API about a PR's current state."""

    state: str  # "open" or "closed"
    merged: bool
    reviews: tuple[Review, ...]
