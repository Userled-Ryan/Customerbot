"""Unit tests for LinearGateway.count_active_se_load — the live per-SE active
issue tally that drives the intake round-robin. `_post` is stubbed so we assert
the tally / zero-seed / fallback-to-None behaviour without hitting the API."""

from __future__ import annotations

from typing import Any

import pytest

from customerbot.integration.linear.gateway import LinearGateway

_USER_MAP = {"U_SE": "lin_se", "U_ELIZA": "lin_eliza"}


def _gateway(
    *, project_id: str | None = "proj_prod", se_project_id: str | None = "proj_se"
) -> LinearGateway:
    return LinearGateway(
        api_token="tok",
        team_id="team",
        project_id=project_id,
        se_project_id=se_project_id,
        user_map=_USER_MAP,
    )


def _stub_post(gw: LinearGateway, result: dict[str, Any] | None) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_post(query: str, variables: dict[str, Any]) -> dict[str, Any] | None:
        captured["query"] = query
        captured["variables"] = variables
        return result

    gw._post = fake_post  # type: ignore[method-assign]
    return captured


@pytest.mark.asyncio
async def test_tally_by_slack_id_with_zero_seed() -> None:
    gw = _gateway()
    captured = _stub_post(
        gw,
        {
            "issues": {
                "nodes": [
                    {"assignee": {"id": "lin_eliza"}},
                    {"assignee": {"id": "lin_eliza"}},
                    {"assignee": {"id": "lin_se"}},
                ],
                "pageInfo": {"hasNextPage": False},
            }
        },
    )

    counts = await gw.count_active_se_load(["U_SE", "U_ELIZA"])

    assert counts == {"U_SE": 1, "U_ELIZA": 2}
    # Scoped to both customerbot projects, filtered to the mapped assignees.
    assert set(captured["variables"]["projectIds"]) == {"proj_se", "proj_prod"}
    assert set(captured["variables"]["assigneeIds"]) == {"lin_se", "lin_eliza"}
    assert "completed" in captured["variables"]["exTypes"]
    assert "In Review" in captured["variables"]["exNames"]


@pytest.mark.asyncio
async def test_member_with_no_active_issues_seeded_zero() -> None:
    gw = _gateway()
    _stub_post(
        gw,
        {"issues": {"nodes": [{"assignee": {"id": "lin_se"}}], "pageInfo": {"hasNextPage": False}}},
    )

    counts = await gw.count_active_se_load(["U_SE", "U_ELIZA"])

    assert counts == {"U_SE": 1, "U_ELIZA": 0}


@pytest.mark.asyncio
async def test_unmapped_se_returns_none_without_querying() -> None:
    gw = _gateway()
    captured = _stub_post(gw, {"issues": {"nodes": []}})

    # U_NEW isn't in the user map → can't compare fairly → None, no query.
    counts = await gw.count_active_se_load(["U_SE", "U_NEW"])

    assert counts is None
    assert captured == {}


@pytest.mark.asyncio
async def test_unresolved_projects_return_none() -> None:
    gw = _gateway(project_id=None, se_project_id=None)
    captured = _stub_post(gw, {"issues": {"nodes": []}})

    counts = await gw.count_active_se_load(["U_SE", "U_ELIZA"])

    assert counts is None
    assert captured == {}


@pytest.mark.asyncio
async def test_post_failure_returns_none() -> None:
    gw = _gateway()
    _stub_post(gw, None)  # simulates Linear unreachable / GraphQL errors

    counts = await gw.count_active_se_load(["U_SE", "U_ELIZA"])

    assert counts is None
