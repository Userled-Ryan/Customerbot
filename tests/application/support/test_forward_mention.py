"""`ForwardSupportMention`: a bot @-mention elsewhere is forwarded into
#userled-support, with the author, source channel, message text, and a thread
link. No-ops when the target channel is unset, when the mention is already in
the support channel, or in a DM.
"""

from __future__ import annotations

import json

import pytest

from customerbot.application.support.forward_mention import ForwardSupportMention
from tests.conftest import FakeSlackPort

_SUPPORT_CHANNEL = "C0ARYPD3E5A"
_SOURCE_CHANNEL = "C_CUST"
_THREAD_TS = "1784803061.369549"
_MESSAGE_TS = "1784807461.052319"
_SENDER = "U08LLQK86FR"


def _blocks_text(blocks: list[dict[str, object]]) -> str:
    """Flatten all text found in a Block Kit payload for substring assertions."""
    return json.dumps(blocks)


@pytest.mark.asyncio
async def test_forwards_to_support_channel(fake_slack: FakeSlackPort) -> None:
    uc = ForwardSupportMention(slack=fake_slack, support_channel_id=_SUPPORT_CHANNEL)

    forwarded = await uc.execute(
        channel_id=_SOURCE_CHANNEL,
        thread_ts=_THREAD_TS,
        message_ts=_MESSAGE_TS,
        sender_user_id=_SENDER,
        text="CC'ing our <@U0B1XJP7H7A|UserledSupport> team to take a look",
    )

    assert forwarded is True
    assert len(fake_slack.blocks_posted) == 1
    channel_id, blocks, _text = fake_slack.blocks_posted[0]
    assert channel_id == _SUPPORT_CHANNEL
    body = _blocks_text(blocks)
    # Author, source channel, message text, and a link to the exact message.
    assert f"<@{_SENDER}>" in body
    assert f"<#{_SOURCE_CHANNEL}>" in body
    assert "take a look" in body
    assert fake_slack.build_thread_link(_SOURCE_CHANNEL, _MESSAGE_TS) in body


@pytest.mark.asyncio
async def test_noop_when_channel_unset(fake_slack: FakeSlackPort) -> None:
    uc = ForwardSupportMention(slack=fake_slack, support_channel_id=None)

    forwarded = await uc.execute(
        channel_id=_SOURCE_CHANNEL,
        thread_ts=_THREAD_TS,
        message_ts=_MESSAGE_TS,
        sender_user_id=_SENDER,
        text="hey",
    )

    assert forwarded is False
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_skips_mention_inside_support_channel(fake_slack: FakeSlackPort) -> None:
    uc = ForwardSupportMention(slack=fake_slack, support_channel_id=_SUPPORT_CHANNEL)

    forwarded = await uc.execute(
        channel_id=_SUPPORT_CHANNEL,
        thread_ts=_THREAD_TS,
        message_ts=_MESSAGE_TS,
        sender_user_id=_SENDER,
        text="hey",
    )

    assert forwarded is False
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_skips_dm(fake_slack: FakeSlackPort) -> None:
    uc = ForwardSupportMention(slack=fake_slack, support_channel_id=_SUPPORT_CHANNEL)

    forwarded = await uc.execute(
        channel_id="D0123ABCD",
        thread_ts=_THREAD_TS,
        message_ts=_MESSAGE_TS,
        sender_user_id=_SENDER,
        text="hey",
    )

    assert forwarded is False
    assert fake_slack.blocks_posted == []


@pytest.mark.asyncio
async def test_skips_bot_sender(fake_slack: FakeSlackPort) -> None:
    uc = ForwardSupportMention(
        slack=fake_slack, support_channel_id=_SUPPORT_CHANNEL, bot_user_id="U_BOT"
    )

    forwarded = await uc.execute(
        channel_id=_SOURCE_CHANNEL,
        thread_ts=_THREAD_TS,
        message_ts=_MESSAGE_TS,
        sender_user_id="U_BOT",
        text="hey",
    )

    assert forwarded is False
    assert fake_slack.blocks_posted == []
