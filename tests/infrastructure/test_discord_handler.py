from prbot.integration.discord.handler import build_scope_keys


class TestBuildScopeKeys:
    def test_full_scope(self) -> None:
        keys = build_scope_keys(guild="G123", channel="C456")
        assert keys == ["discord/G123/C456", "discord/G123", "discord"]

    def test_guild_only(self) -> None:
        keys = build_scope_keys(guild="G123", channel="")
        assert keys == ["discord/G123", "discord"]

    def test_no_guild_no_channel(self) -> None:
        keys = build_scope_keys(guild="", channel="")
        assert keys == ["discord"]
