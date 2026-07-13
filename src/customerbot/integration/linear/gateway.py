"""Linear GraphQL adapter (v1.5).

`LinearGateway` implements `LinearPort` over Linear's GraphQL API using aiohttp.
Like `SlackGateway`, every method swallows + logs errors and returns
`None`/`False` — Linear is best-effort and must never raise into the hot path.
A short per-call timeout means Linear latency can't stall a Slack interaction.

`NoOpLinearGateway` is the stand-in when Linear is unconfigured, so the rest of
the app (and the whole test suite) wires up unconditionally and stays green.

`resolve_workspace_ids()` runs once at startup and fills in everything the
deploy didn't hand us — the bot's own actor id, the workflow `stateId`s (matched
by name against the configured logical states), and the Product Responder /
SE Responder project ids — so the owner only has to supply an API token + team id.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from customerbot.domain.linear.ports import LinearIssueRef, LinearWorkflowState
from customerbot.integration.linear.mapping import first_github_pr_url

logger = logging.getLogger(__name__)

INTEGRATION_ID = "linear"

_API_URL = "https://api.linear.app/graphql"

# Substrings (lowercased) used to match a team's real workflow-state names to
# our logical states during auto-resolution. First match wins.
_STATE_NAME_HINTS: dict[LinearWorkflowState, tuple[str, ...]] = {
    LinearWorkflowState.TRIAGE: ("triage", "todo", "backlog"),
    LinearWorkflowState.IN_PROGRESS: ("in progress", "started", "in-progress"),
    LinearWorkflowState.AWAITING_CUSTOMER: ("awaiting customer", "awaiting", "customer"),
    LinearWorkflowState.DONE: ("done", "completed", "resolved"),
    LinearWorkflowState.CANCELED: ("canceled", "cancelled"),
}


class LinearGateway:
    """Adapter: wraps Linear's GraphQL API for the ticket mirror."""

    def __init__(
        self,
        *,
        api_token: str,
        team_id: str,
        project_id: str | None = None,
        se_project_id: str | None = None,
        workflow_states: dict[str, str] | None = None,
        actor_id: str | None = None,
        user_map: dict[str, str] | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._api_token = api_token
        self._team_id = team_id
        self._project_id = project_id
        self._se_project_id = se_project_id
        # logical-state value -> Linear stateId UUID
        self._state_ids: dict[str, str] = dict(workflow_states or {})
        self._actor_id = actor_id
        # Slack user id -> Linear user id, for mirroring the SE owner as assignee.
        self._user_map: dict[str, str] = dict(user_map or {})
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        # namespaced cache key ("org:…" / "type:…") -> labelId
        self._label_cache: dict[str, str] = {}

    @property
    def actor_id(self) -> str | None:
        return self._actor_id

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"Authorization": self._api_token}, timeout=self._timeout
        )
        await self.resolve_workspace_ids()

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # -- low-level GraphQL --------------------------------------------------

    async def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any] | None:
        if self._session is None:
            logger.warning("LinearGateway used before start(); ignoring call")
            return None
        try:
            async with self._session.post(
                _API_URL, json={"query": query, "variables": variables}
            ) as resp:
                payload = await resp.json()
            if payload.get("errors"):
                logger.error("Linear GraphQL errors: %s", payload["errors"])
                return None
            return payload.get("data")
        except Exception:
            logger.exception("Linear GraphQL request failed")
            return None

    def _state_id(self, state: LinearWorkflowState) -> str | None:
        sid = self._state_ids.get(state.value)
        if sid is None:
            logger.error("No Linear stateId configured/resolved for %s", state.value)
        return sid

    # -- startup resolution -------------------------------------------------

    async def resolve_workspace_ids(self) -> None:
        """Fill actor id, workflow stateIds, and the project id from the API.

        Anything already supplied via config is left untouched. Best-effort:
        failures just leave the gaps and are logged.
        """
        data = await self._post(
            """
            query Bootstrap($teamId: String!) {
              viewer { id }
              team(id: $teamId) {
                states { nodes { id name type } }
              }
              projects(first: 250) { nodes { id name } }
            }
            """,
            {"teamId": self._team_id},
        )
        if data is None:
            return

        if self._actor_id is None:
            viewer = data.get("viewer") or {}
            self._actor_id = viewer.get("id")

        team = data.get("team") or {}
        states = (team.get("states") or {}).get("nodes") or []
        for logical, hints in _STATE_NAME_HINTS.items():
            if logical.value in self._state_ids:
                continue  # honour explicit config
            match = _match_state(states, hints)
            if match is not None:
                self._state_ids[logical.value] = match

        # Fallback: many teams don't have a dedicated "Awaiting Customer" state.
        # Map it to Done so no custom Linear setup is required — if a real
        # awaiting state is added later it's matched above and wins.
        if (
            LinearWorkflowState.AWAITING_CUSTOMER.value not in self._state_ids
            and LinearWorkflowState.DONE.value in self._state_ids
        ):
            self._state_ids[LinearWorkflowState.AWAITING_CUSTOMER.value] = self._state_ids[
                LinearWorkflowState.DONE.value
            ]

        if self._project_id is None or self._se_project_id is None:
            projects = (data.get("projects") or {}).get("nodes") or []
            for proj in projects:
                name = str(proj.get("name", "")).lower()
                if self._project_id is None and "product responder" in name:
                    self._project_id = proj.get("id")
                elif self._se_project_id is None and "se responder" in name:
                    self._se_project_id = proj.get("id")

    # -- LinearPort ---------------------------------------------------------

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
        issue_input: dict[str, Any] = {
            "teamId": self._team_id,
            "title": title,
            "description": description,
            "priority": priority,
        }
        state_id = self._state_id(state)
        if state_id is not None:
            issue_input["stateId"] = state_id
        if label_ids:
            issue_input["labelIds"] = label_ids
        # Dev-lane issues land in Product Responder, SE-lane in SE Responder. An
        # issue belongs to at most one project, so setting projectId later (via
        # add_to_project / add_to_se_project) moves it between the two.
        if in_project and self._project_id is not None:
            issue_input["projectId"] = self._project_id
        elif in_se_project and self._se_project_id is not None:
            issue_input["projectId"] = self._se_project_id
        assignee_id = self._linear_user_id(assignee_slack_id)
        if assignee_id is not None:
            issue_input["assigneeId"] = assignee_id

        data = await self._post(
            """
            mutation CreateIssue($input: IssueCreateInput!) {
              issueCreate(input: $input) {
                success
                issue { id identifier url }
              }
            }
            """,
            {"input": issue_input},
        )
        issue = ((data or {}).get("issueCreate") or {}).get("issue")
        if not issue:
            return None
        return LinearIssueRef(
            issue_id=issue["id"], identifier=issue["identifier"], url=issue["url"]
        )

    async def update_issue_state(self, *, issue_id: str, state: LinearWorkflowState) -> bool:
        state_id = self._state_id(state)
        if state_id is None:
            return False
        data = await self._post(
            """
            mutation UpdateState($id: String!, $stateId: String!) {
              issueUpdate(id: $id, input: { stateId: $stateId }) { success }
            }
            """,
            {"id": issue_id, "stateId": state_id},
        )
        return bool(((data or {}).get("issueUpdate") or {}).get("success"))

    async def update_issue_priority(self, *, issue_id: str, priority: int) -> bool:
        data = await self._post(
            """
            mutation UpdatePriority($id: String!, $priority: Int!) {
              issueUpdate(id: $id, input: { priority: $priority }) { success }
            }
            """,
            {"id": issue_id, "priority": priority},
        )
        return bool(((data or {}).get("issueUpdate") or {}).get("success"))

    async def add_comment(self, *, issue_id: str, body: str) -> bool:
        data = await self._post(
            """
            mutation AddComment($issueId: String!, $body: String!) {
              commentCreate(input: { issueId: $issueId, body: $body }) { success }
            }
            """,
            {"issueId": issue_id, "body": body},
        )
        return bool(((data or {}).get("commentCreate") or {}).get("success"))

    async def add_to_project(self, *, issue_id: str) -> bool:
        if self._project_id is None:
            logger.warning("No Product Responder project id resolved; can't add %s", issue_id)
            return False
        return await self._set_project(issue_id=issue_id, project_id=self._project_id)

    async def add_to_se_project(self, *, issue_id: str) -> bool:
        if self._se_project_id is None:
            logger.warning("No SE Responder project id resolved; can't add %s", issue_id)
            return False
        return await self._set_project(issue_id=issue_id, project_id=self._se_project_id)

    async def _set_project(self, *, issue_id: str, project_id: str) -> bool:
        data = await self._post(
            """
            mutation SetProject($id: String!, $projectId: String!) {
              issueUpdate(id: $id, input: { projectId: $projectId }) { success }
            }
            """,
            {"id": issue_id, "projectId": project_id},
        )
        return bool(((data or {}).get("issueUpdate") or {}).get("success"))

    def _linear_user_id(self, slack_user_id: str | None) -> str | None:
        """Translate a Slack user id to a Linear user id via the configured map."""
        if not slack_user_id:
            return None
        return self._user_map.get(slack_user_id)

    async def assign_issue(self, *, issue_id: str, slack_user_id: str | None) -> bool:
        """Set the issue assignee from a Slack user id (mapped to a Linear user).

        No-op returning `False` when the owner isn't in the configured user map,
        so an unmapped SE owner simply leaves the Linear issue unassigned.
        """
        assignee_id = self._linear_user_id(slack_user_id)
        if assignee_id is None:
            return False
        data = await self._post(
            """
            mutation AssignIssue($id: String!, $assigneeId: String!) {
              issueUpdate(id: $id, input: { assigneeId: $assigneeId }) { success }
            }
            """,
            {"id": issue_id, "assigneeId": assignee_id},
        )
        return bool(((data or {}).get("issueUpdate") or {}).get("success"))

    async def ensure_org_label(self, *, org_id: str, name: str) -> str | None:
        return await self._ensure_label(cache_key=f"org:{org_id}", name=name)

    async def ensure_type_label(self, *, ticket_type: str, name: str) -> str | None:
        return await self._ensure_label(cache_key=f"type:{ticket_type}", name=name)

    async def _ensure_label(self, *, cache_key: str, name: str) -> str | None:
        """Resolve a label id by display name, creating it if absent.

        `cache_key` is namespaced by caller (`org:…` / `type:…`) so an org and a
        ticket type can never collide in the in-process cache.
        """
        if cache_key in self._label_cache:
            return self._label_cache[cache_key]

        # Look up an existing label by name first (idempotent across restarts).
        # Scope to our team: label names can collide across teams, and a label
        # belonging to another team can't be attached to our issues (Linear
        # rejects "labelIds for incorrect team"). This matches the create path
        # below, which is team-scoped via teamId.
        found = await self._post(
            """
            query FindLabel($name: String!, $teamId: ID!) {
              issueLabels(
                filter: { name: { eq: $name }, team: { id: { eq: $teamId } } }
                first: 1
              ) {
                nodes { id }
              }
            }
            """,
            {"name": name, "teamId": self._team_id},
        )
        nodes = ((found or {}).get("issueLabels") or {}).get("nodes") or []
        if nodes:
            label_id = nodes[0]["id"]
            self._label_cache[cache_key] = label_id
            return label_id

        created = await self._post(
            """
            mutation CreateLabel($name: String!, $teamId: String!) {
              issueLabelCreate(input: { name: $name, teamId: $teamId }) {
                issueLabel { id }
              }
            }
            """,
            {"name": name, "teamId": self._team_id},
        )
        label = ((created or {}).get("issueLabelCreate") or {}).get("issueLabel")
        if not label:
            return None
        self._label_cache[cache_key] = label["id"]
        return label["id"]

    async def add_label(self, *, issue_id: str, label_id: str) -> bool:
        data = await self._post(
            """
            mutation AddLabel($id: String!, $labelId: String!) {
              issueAddLabel(id: $id, labelId: $labelId) { success }
            }
            """,
            {"id": issue_id, "labelId": label_id},
        )
        return bool(((data or {}).get("issueAddLabel") or {}).get("success"))

    async def remove_label(self, *, issue_id: str, label_id: str) -> bool:
        data = await self._post(
            """
            mutation RemoveLabel($id: String!, $labelId: String!) {
              issueRemoveLabel(id: $id, labelId: $labelId) { success }
            }
            """,
            {"id": issue_id, "labelId": label_id},
        )
        return bool(((data or {}).get("issueRemoveLabel") or {}).get("success"))

    async def get_issue_state(self, *, issue_id: str) -> LinearWorkflowState | None:
        data = await self._post(
            """
            query IssueState($id: String!) {
              issue(id: $id) { state { id } }
            }
            """,
            {"id": issue_id},
        )
        state = ((data or {}).get("issue") or {}).get("state") or {}
        state_id = state.get("id")
        if not state_id:
            return None
        for logical_value, sid in self._state_ids.items():
            if sid == state_id:
                return LinearWorkflowState(logical_value)
        return None

    async def get_issue_pr_link(self, *, issue_id: str) -> str | None:
        data = await self._post(
            """
            query IssuePR($id: String!) {
              issue(id: $id) {
                description
                attachments { nodes { url } }
              }
            }
            """,
            {"id": issue_id},
        )
        issue = (data or {}).get("issue") or {}
        nodes = ((issue.get("attachments") or {}).get("nodes")) or []
        candidates = [node.get("url") for node in nodes]
        candidates.append(issue.get("description"))
        return first_github_pr_url(candidates)


def _match_state(states: list[dict[str, Any]], hints: tuple[str, ...]) -> str | None:
    """Return the stateId of the first team state whose name matches a hint."""
    for hint in hints:
        for node in states:
            if hint in str(node.get("name", "")).lower():
                return node.get("id")
    return None


class NoOpLinearGateway:
    """Used when Linear is unconfigured — every operation is a silent no-op."""

    actor_id: str | None = None

    async def start(self) -> None:  # pragma: no cover - trivial
        return None

    async def stop(self) -> None:  # pragma: no cover - trivial
        return None

    async def resolve_workspace_ids(self) -> None:  # pragma: no cover - trivial
        return None

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
        return None

    async def update_issue_state(self, *, issue_id: str, state: LinearWorkflowState) -> bool:
        return False

    async def update_issue_priority(self, *, issue_id: str, priority: int) -> bool:
        return False

    async def add_comment(self, *, issue_id: str, body: str) -> bool:
        return False

    async def add_to_project(self, *, issue_id: str) -> bool:
        return False

    async def add_to_se_project(self, *, issue_id: str) -> bool:
        return False

    async def assign_issue(self, *, issue_id: str, slack_user_id: str | None) -> bool:
        return False

    async def ensure_org_label(self, *, org_id: str, name: str) -> str | None:
        return None

    async def ensure_type_label(self, *, ticket_type: str, name: str) -> str | None:
        return None

    async def add_label(self, *, issue_id: str, label_id: str) -> bool:
        return False

    async def remove_label(self, *, issue_id: str, label_id: str) -> bool:
        return False

    async def get_issue_state(self, *, issue_id: str) -> LinearWorkflowState | None:
        return None

    async def get_issue_pr_link(self, *, issue_id: str) -> str | None:
        return None
