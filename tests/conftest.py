"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
    run_migrations,
)
from customerbot.domain.linear.ports import LinearIssueRef, LinearWorkflowState
from customerbot.domain.messaging.ports import ThreadMessage


@pytest_asyncio.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "test.db"
    url = database_url_from_path(str(db_path))
    run_migrations(url)
    engine = make_engine(url)
    factory = make_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


@dataclass
class FakeSlackPort:
    """In-memory recorder implementing `SlackPort`. Reused across pipeline tests."""

    workspace_url: str = "https://test.slack.com"
    next_view_id: str = "V_TEST"
    next_message_ts: str = "1700000000.000100"
    dms_sent: list[tuple[str, str]] = field(default_factory=list)
    messages_sent: list[tuple[str, str, str | None]] = field(default_factory=list)
    blocks_posted: list[tuple[str, list[dict[str, Any]], str]] = field(default_factory=list)
    messages_updated: list[tuple[str, str, list[dict[str, Any]], str]] = field(default_factory=list)
    views_opened: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    views_updated: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    dm_blocks_sent: list[tuple[str, list[dict[str, Any]], str]] = field(default_factory=list)
    user_group_memberships: dict[str, set[str]] = field(default_factory=dict)
    thread_messages: dict[tuple[str, str], list[ThreadMessage]] = field(default_factory=dict)
    reactions_added: list[tuple[str, str, str]] = field(default_factory=list)
    reactions_removed: list[tuple[str, str, str]] = field(default_factory=list)
    ephemerals_sent: list[tuple[str, str, str]] = field(default_factory=list)
    user_display_names: dict[str, str] = field(default_factory=dict)

    async def send_dm(self, user_id: str, text: str) -> None:
        self.dms_sent.append((user_id, text))

    async def send_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.messages_sent.append((channel_id, text, thread_ts))

    async def send_ephemeral(self, channel_id: str, user_id: str, text: str) -> None:
        self.ephemerals_sent.append((channel_id, user_id, text))

    async def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> str | None:
        self.blocks_posted.append((channel_id, blocks, text))
        return self.next_message_ts

    async def update_message(
        self,
        channel_id: str,
        message_ts: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> None:
        self.messages_updated.append((channel_id, message_ts, blocks, text))

    async def open_view(self, trigger_id: str, view: dict[str, Any]) -> str | None:
        self.views_opened.append((trigger_id, view))
        return self.next_view_id

    async def update_view(self, view_id: str, view: dict[str, Any]) -> None:
        self.views_updated.append((view_id, view))

    async def get_channel_name(self, channel_id: str) -> str:
        return channel_id

    async def get_user_display_name(self, user_id: str) -> str:
        return self.user_display_names.get(user_id, user_id)

    async def send_dm_blocks(
        self,
        user_id: str,
        blocks: list[dict[str, Any]],
        *,
        text: str = "",
    ) -> tuple[str, str] | None:
        self.dm_blocks_sent.append((user_id, blocks, text))
        return (f"D_for_{user_id}", self.next_message_ts)

    async def is_user_in_group(self, user_id: str, group_id: str) -> bool:
        return user_id in self.user_group_memberships.get(group_id, set())

    async def list_group_members(self, group_id: str) -> list[str]:
        return sorted(self.user_group_memberships.get(group_id, set()))

    async def get_thread_messages(
        self, channel_id: str, thread_ts: str, *, limit: int = 5
    ) -> list[ThreadMessage]:
        msgs = self.thread_messages.get((channel_id, thread_ts), [])
        return list(msgs)[-limit:]

    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> None:
        self.reactions_added.append((channel_id, ts, emoji))

    async def remove_reaction(self, channel_id: str, ts: str, emoji: str) -> None:
        self.reactions_removed.append((channel_id, ts, emoji))

    def build_thread_link(self, channel_id: str, thread_ts: str) -> str:
        clean = thread_ts.replace(".", "")
        return f"{self.workspace_url}/archives/{channel_id}/p{clean}"

    def parse_thread_link(self, link: str) -> tuple[str, str] | None:
        marker = "/archives/"
        idx = link.find(marker)
        if idx == -1:
            return None
        parts = link[idx + len(marker) :].split("/")
        if len(parts) < 2:
            return None
        channel_id, ts_token = parts[0], parts[1]
        digits = ts_token.lstrip("p").split("?")[0].split("-")[0]
        if not channel_id or not digits.isdigit() or len(digits) <= 6:
            return None
        return channel_id, f"{digits[:-6]}.{digits[-6:]}"


@pytest.fixture
def fake_slack() -> FakeSlackPort:
    return FakeSlackPort()


@dataclass
class FakeLinearPort:
    """In-memory recorder implementing `LinearPort`."""

    actor_id: str | None = "U_BOT_LINEAR"
    next_issue_seq: int = 1
    raise_on_create: bool = False
    created_issues: list[dict[str, Any]] = field(default_factory=list)
    state_updates: list[tuple[str, LinearWorkflowState]] = field(default_factory=list)
    priority_updates: list[tuple[str, int]] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)
    project_adds: list[str] = field(default_factory=list)
    se_project_adds: list[str] = field(default_factory=list)
    assignments: list[tuple[str, str | None]] = field(default_factory=list)  # (issue_id, slack_id)
    labels: dict[str, str] = field(default_factory=dict)  # org_id -> labelId
    type_labels: dict[str, str] = field(default_factory=dict)  # type value -> labelId
    label_adds: list[tuple[str, str]] = field(default_factory=list)  # (issue_id, labelId)
    label_removes: list[tuple[str, str]] = field(default_factory=list)  # (issue_id, labelId)
    issue_states: dict[str, LinearWorkflowState] = field(default_factory=dict)
    pr_links: dict[str, str] = field(default_factory=dict)  # issue_id -> PR url
    linear_to_slack: dict[str, str] = field(default_factory=dict)  # Linear user id -> Slack id

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
        if self.raise_on_create:
            raise RuntimeError("simulated Linear outage")
        seq = self.next_issue_seq
        self.next_issue_seq += 1
        issue_id = f"lin_{seq}"
        self.created_issues.append(
            {
                "issue_id": issue_id,
                "title": title,
                "description": description,
                "state": state,
                "priority": priority,
                "label_ids": list(label_ids),
                "in_project": in_project,
                "in_se_project": in_se_project,
                "assignee_slack_id": assignee_slack_id,
            }
        )
        if in_project:
            self.project_adds.append(issue_id)
        if in_se_project:
            self.se_project_adds.append(issue_id)
        if assignee_slack_id is not None:
            self.assignments.append((issue_id, assignee_slack_id))
        self.issue_states[issue_id] = state
        return LinearIssueRef(
            issue_id=issue_id,
            identifier=f"PRD-{seq}",
            url=f"https://linear.app/userledio/issue/PRD-{seq}",
        )

    async def update_issue_state(self, *, issue_id: str, state: LinearWorkflowState) -> bool:
        self.state_updates.append((issue_id, state))
        self.issue_states[issue_id] = state
        return True

    async def update_issue_priority(self, *, issue_id: str, priority: int) -> bool:
        self.priority_updates.append((issue_id, priority))
        return True

    async def add_comment(self, *, issue_id: str, body: str) -> bool:
        self.comments.append((issue_id, body))
        return True

    async def add_to_project(self, *, issue_id: str) -> bool:
        self.project_adds.append(issue_id)
        return True

    async def add_to_se_project(self, *, issue_id: str) -> bool:
        self.se_project_adds.append(issue_id)
        return True

    async def assign_issue(self, *, issue_id: str, slack_user_id: str | None) -> bool:
        self.assignments.append((issue_id, slack_user_id))
        return slack_user_id is not None

    async def slack_user_for_linear_id(self, linear_user_id: str | None) -> str | None:
        if not linear_user_id:
            return None
        return self.linear_to_slack.get(linear_user_id)

    async def ensure_org_label(self, *, org_id: str, name: str) -> str | None:
        label_id = self.labels.setdefault(org_id, f"label_{org_id}")
        return label_id

    async def ensure_type_label(self, *, ticket_type: str, name: str) -> str | None:
        return self.type_labels.setdefault(ticket_type, f"typelabel_{ticket_type}")

    async def add_label(self, *, issue_id: str, label_id: str) -> bool:
        self.label_adds.append((issue_id, label_id))
        return True

    async def remove_label(self, *, issue_id: str, label_id: str) -> bool:
        self.label_removes.append((issue_id, label_id))
        return True

    async def get_issue_state(self, *, issue_id: str) -> LinearWorkflowState | None:
        return self.issue_states.get(issue_id)

    async def get_issue_pr_link(self, *, issue_id: str) -> str | None:
        return self.pr_links.get(issue_id)


@pytest.fixture
def fake_linear() -> FakeLinearPort:
    return FakeLinearPort()
