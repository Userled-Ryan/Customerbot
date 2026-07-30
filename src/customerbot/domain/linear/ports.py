"""Domain port for the Linear mirror (v1.5).

`LinearPort` is the narrow surface the application layer depends on — the same
way it depends on `SlackPort`. Every method returns `None`/`False` (rather than
raising) on failure, so the "Linear is best-effort, never load-bearing"
contract is enforceable at the type level: callers can ignore the result and
the Slack flow is never interrupted by a Linear hiccup.

Logical workflow states (`LinearWorkflowState`) are mapped to a team's real
Linear `stateId` UUIDs inside the adapter (`LinearGateway`), driven by config.
That keeps the application layer talking in customerbot-aligned vocabulary and
isolates the workspace-specific IDs to the edge.
"""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class LinearWorkflowState(StrEnum):
    """Logical states, configured to mirror customerbot statuses 1:1.

    The team's Linear workflow is set up with matching named states; the
    adapter resolves each of these to a `stateId` at startup.
    """

    TRIAGE = "triage"  # ← customerbot NEW
    URGENT = "urgent"  # ← NEW + urgent flag (intake "Urgent" checkbox)
    IN_PROGRESS = "in_progress"  # ← IN_PROGRESS (and dev-lane open work)
    AWAITING_CUSTOMER = "awaiting_customer"  # ← AWAITING_CUSTOMER
    DONE = "done"  # ← RESOLVED / auto-closed
    CANCELED = "canceled"  # ← CLOSED via Drop


class LinearIssueRef(BaseModel, frozen=True):
    """Identifiers returned when an issue is created — persisted on the ticket."""

    issue_id: str  # internal UUID, used for mutations + inbound lookup
    identifier: str  # human key, e.g. "PRD-123"
    url: str  # deep link, surfaced in SE/stakeholder DMs


class LinearPort(Protocol):
    """Best-effort Linear operations. All methods swallow errors at the edge."""

    async def create_issue(
        self,
        *,
        title: str,
        description: str,
        state: LinearWorkflowState,
        priority: int,
        label_ids: list[str],
        in_project: bool = False,
        in_se_project: bool = False,
        assignee_slack_id: str | None = None,
    ) -> LinearIssueRef | None:
        """Create an issue in the configured team. `in_project=True` adds it to
        the Product Responder project (the dev queue); `in_se_project=True` adds
        it to the SE Responder project (the SE queue) — at most one applies.
        `assignee_slack_id` sets the assignee if it maps to a Linear user.
        Returns the new issue's ref, or `None` on any failure."""
        ...

    async def update_issue_state(self, *, issue_id: str, state: LinearWorkflowState) -> bool: ...

    async def update_issue_priority(self, *, issue_id: str, priority: int) -> bool:
        """Set the issue's priority (Linear scale: 0 none … 4 low). Returns
        `False` on any failure."""
        ...

    async def add_comment(self, *, issue_id: str, body: str) -> bool: ...

    async def add_to_project(self, *, issue_id: str) -> bool:
        """Add an existing issue to the Product Responder project (dev queue).
        Moves it out of any other project (an issue has one project)."""
        ...

    async def add_to_se_project(self, *, issue_id: str) -> bool:
        """Add an existing issue to the SE Responder project (SE queue). Moves
        it out of any other project (an issue has one project)."""
        ...

    async def assign_issue(self, *, issue_id: str, slack_user_id: str | None) -> bool:
        """Set the issue assignee from a Slack user id (mapped to a Linear user
        via config). Returns `False` when the user isn't mapped or on failure."""
        ...

    def has_linear_user(self, slack_user_id: str) -> bool:
        """Whether this Slack user maps to a Linear user, i.e. whether
        `assign_issue` would actually land for them. Used to pick the dev to
        assign out of the `@support` group, whose membership can include people
        with no Linear mapping (for whom an assign is a silent no-op)."""
        ...

    async def slack_user_for_linear_id(self, linear_user_id: str | None) -> str | None:
        """Reverse of the assignee map: a Linear user id → the Slack user id, or
        `None` if the input is `None` or the Linear user isn't in the config map.
        Used inbound to mirror a Linear assignee change back to the SE owner."""
        ...

    async def count_active_se_load(
        self, pool_slack_ids: Collection[str]
    ) -> dict[str, int] | None:
        """Count each pooled SE's active customerbot issues in Linear — issues in
        the SE Responder + Product Responder projects assigned to them, excluding
        the Done / Canceled / Duplicate / In Review states. Keys are Slack user
        ids; pooled members with zero active issues are present with `0`.

        Returns `None` when the count can't be computed fairly — Linear
        unreachable, project ids unresolved, or any pooled SE has no Linear-user
        mapping — signalling the caller to fall back to the local ticket count.
        Used by the intake round-robin to balance new tickets by real workload."""
        ...

    async def ensure_org_label(self, *, org_id: str, name: str) -> str | None:
        """Look up (or create) the per-org label and return its labelId.

        Keyed off `org_id` so a later rename never splits reporting; `name` is
        the display label. Returns `None` if Linear is unreachable.
        """
        ...

    async def ensure_type_label(self, *, ticket_type: str, name: str) -> str | None:
        """Look up (or create) the per-type label ("Bug"/"Config"/"FAQ") and
        return its labelId, so Linear reports can filter issues by ticket type.

        Keyed off the type value; `name` is the display label. Returns `None`
        if Linear is unreachable.
        """
        ...

    async def add_label(self, *, issue_id: str, label_id: str) -> bool:
        """Add a single label to an existing issue (leaves other labels intact).
        Returns `False` on any failure."""
        ...

    async def remove_label(self, *, issue_id: str, label_id: str) -> bool:
        """Remove a single label from an existing issue. Returns `False` on any
        failure."""
        ...

    async def get_issue_state(self, *, issue_id: str) -> LinearWorkflowState | None:
        """Current logical state of an issue, or `None` if unknown/unreachable.
        Used by the reconcile sweep to detect drift."""
        ...

    async def get_issue_pr_link(self, *, issue_id: str) -> str | None:
        """The GitHub PR link attached to an issue, or `None` if there isn't one
        (or Linear is unreachable). Reads the issue's attachments (Linear's
        GitHub integration attaches PRs there) and its description. Used when a
        dev marks the issue Done so the resolve is recorded as a code change
        with the PR when one exists, and a no-code-change resolve otherwise."""
        ...
