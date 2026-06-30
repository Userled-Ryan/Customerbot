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

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class LinearWorkflowState(StrEnum):
    """Logical states, configured to mirror customerbot statuses 1:1.

    The team's Linear workflow is set up with matching named states; the
    adapter resolves each of these to a `stateId` at startup.
    """

    TRIAGE = "triage"  # ← customerbot NEW
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
    ) -> LinearIssueRef | None:
        """Create an issue in the configured team. `in_project=True` also adds
        it to the Product Responder project (the dev queue). Returns the new
        issue's ref, or `None` on any failure."""
        ...

    async def update_issue_state(self, *, issue_id: str, state: LinearWorkflowState) -> bool: ...

    async def update_issue_priority(self, *, issue_id: str, priority: int) -> bool:
        """Set the issue's priority (Linear scale: 0 none … 4 low). Returns
        `False` on any failure."""
        ...

    async def add_comment(self, *, issue_id: str, body: str) -> bool: ...

    async def add_to_project(self, *, issue_id: str) -> bool:
        """Add an existing issue to the Product Responder project."""
        ...

    async def ensure_org_label(self, *, org_id: str, name: str) -> str | None:
        """Look up (or create) the per-org label and return its labelId.

        Keyed off `org_id` so a later rename never splits reporting; `name` is
        the display label. Returns `None` if Linear is unreachable.
        """
        ...

    async def get_issue_state(self, *, issue_id: str) -> LinearWorkflowState | None:
        """Current logical state of an issue, or `None` if unknown/unreachable.
        Used by the reconcile sweep to detect drift."""
        ...
