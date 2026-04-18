import pytest

from prbot.application.manage_scope_config import ManageUserExclusions
from tests.conftest import FakeUserExclusionRepo


class TestManageUserExclusions:
    @pytest.fixture
    def repo(self) -> FakeUserExclusionRepo:
        return FakeUserExclusionRepo()

    @pytest.fixture
    def use_case(self, repo: FakeUserExclusionRepo) -> ManageUserExclusions:
        return ManageUserExclusions(exclusion_repo=repo)

    async def test_exclude_user(
        self, use_case: ManageUserExclusions, repo: FakeUserExclusionRepo
    ) -> None:
        result = await use_case.exclude_user("slack/T1/C1", "Cursor")

        assert result.excluded is True
        assert result.was_already is False
        assert await repo.is_excluded(["slack/T1/C1"], "Cursor")

    async def test_exclude_user_already_excluded(
        self, use_case: ManageUserExclusions, repo: FakeUserExclusionRepo
    ) -> None:
        await repo.add("slack/T1/C1", "Cursor")

        result = await use_case.exclude_user("slack/T1/C1", "Cursor")

        assert result.excluded is True
        assert result.was_already is True

    async def test_include_user_removes_from_list(
        self, use_case: ManageUserExclusions, repo: FakeUserExclusionRepo
    ) -> None:
        await repo.add("slack/T1/C1", "Cursor")
        await repo.add("slack/T1/C1", "dependabot[bot]")

        result = await use_case.include_user("slack/T1/C1", "Cursor")

        assert result.excluded is False
        assert result.was_already is False
        assert not await repo.is_excluded(["slack/T1/C1"], "Cursor")
        assert await repo.is_excluded(["slack/T1/C1"], "dependabot[bot]")

    async def test_include_user_not_excluded(self, use_case: ManageUserExclusions) -> None:
        result = await use_case.include_user("slack/T1/C1", "Cursor")

        assert result.excluded is False
        assert result.was_already is True

    async def test_list_excluded_users_empty(self, use_case: ManageUserExclusions) -> None:
        grouped = await use_case.list_excluded_users(["slack/T1/C1"])
        assert grouped == {}

    async def test_list_excluded_users_returns_grouped(
        self, use_case: ManageUserExclusions, repo: FakeUserExclusionRepo
    ) -> None:
        await repo.add("slack/T1/C1", "Cursor")
        await repo.add("slack/T1/C1", "bot")
        await repo.add("slack/T1", "dependabot[bot]")

        grouped = await use_case.list_excluded_users(["slack/T1/C1", "slack/T1"])

        assert sorted(grouped["slack/T1/C1"]) == ["Cursor", "bot"]
        assert grouped["slack/T1"] == ["dependabot[bot]"]

    async def test_list_excluded_users_omits_empty_scopes(
        self, use_case: ManageUserExclusions, repo: FakeUserExclusionRepo
    ) -> None:
        await repo.add("slack/T1", "dependabot[bot]")

        grouped = await use_case.list_excluded_users(["slack/T1/C1", "slack/T1"])

        assert "slack/T1/C1" not in grouped
        assert grouped["slack/T1"] == ["dependabot[bot]"]
