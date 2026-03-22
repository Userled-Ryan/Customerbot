from unittest.mock import AsyncMock

import pytest

from prbot.domain.value_objects import EmojiReaction
from prbot.infrastructure.slack_gateway import SlackGateway


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def gateway(mock_client: AsyncMock) -> SlackGateway:
    return SlackGateway(client=mock_client)


class TestSlackGateway:
    async def test_add_reaction_calls_api(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        await gateway.add_reaction("C123", "1234.5678", EmojiReaction.OPEN)

        mock_client.reactions_add.assert_awaited_once_with(
            channel="C123", timestamp="1234.5678", name="eyes"
        )

    async def test_add_reaction_ignores_already_reacted(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.reactions_add.side_effect = Exception("already_reacted")

        # Should not raise
        await gateway.add_reaction("C123", "1234.5678", EmojiReaction.OPEN)

    async def test_add_reaction_raises_other_errors(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.reactions_add.side_effect = Exception("channel_not_found")

        with pytest.raises(Exception, match="channel_not_found"):
            await gateway.add_reaction("C123", "1234.5678", EmojiReaction.OPEN)

    async def test_remove_reaction_calls_api(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        await gateway.remove_reaction("C123", "1234.5678", EmojiReaction.OPEN)

        mock_client.reactions_remove.assert_awaited_once_with(
            channel="C123", timestamp="1234.5678", name="eyes"
        )

    async def test_remove_reaction_ignores_no_reaction(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.reactions_remove.side_effect = Exception("no_reaction")

        # Should not raise
        await gateway.remove_reaction("C123", "1234.5678", EmojiReaction.OPEN)
