from prbot.domain.tracking.value_objects import PRUrl


class TestPRUrl:
    def test_is_frozen(self) -> None:
        pr = PRUrl(owner="a", repo="b", number=1)
        try:
            pr.owner = "c"  # type: ignore[misc]
            msg = "Should have raised"
            raise AssertionError(msg)
        except Exception:
            pass
