from pydantic import BaseModel


class WebhookRepository(BaseModel):
    full_name: str  # "owner/repo"


class WebhookPullRequest(BaseModel):
    number: int
    state: str  # "open" or "closed"
    merged: bool = False


class WebhookReview(BaseModel):
    state: str  # "approved", "changes_requested", "commented", "dismissed"


class WebhookUser(BaseModel):
    login: str


class PullRequestEvent(BaseModel):
    action: str  # "opened", "closed", "reopened", "synchronize"
    pull_request: WebhookPullRequest
    repository: WebhookRepository
    sender: WebhookUser


class PullRequestReviewEvent(BaseModel):
    action: str  # "submitted", "dismissed"
    review: WebhookReview
    pull_request: WebhookPullRequest
    repository: WebhookRepository
    sender: WebhookUser
