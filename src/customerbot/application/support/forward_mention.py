"""Forward `@UserledSupport` mentions into the #userled-support channel.

When a teammate @-mentions the bot in a thread (e.g. a customer channel —
"CC'ing our @UserledSupport team to take a look"), Slack delivers it as an
`app_mention` event. This use case forwards that message into the internal
support channel (`userled_support_channel_id`) so the support team sees the
request and can jump straight into the source thread.

The `log this` command mention is handled by the intake detector, not here —
the handler only routes genuine mentions to this path.
"""

from __future__ import annotations

import logging
from typing import Any

from customerbot.domain.messaging.ports import SlackPort

logger = logging.getLogger(__name__)


class ForwardSupportMention:
    def __init__(
        self,
        slack: SlackPort,
        support_channel_id: str | None,
        bot_user_id: str | None = None,
    ) -> None:
        self._slack = slack
        self._support_channel_id = support_channel_id
        self._bot_user_id = bot_user_id

    async def execute(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        sender_user_id: str,
        text: str,
    ) -> bool:
        """Forward one bot-mention message into the support channel.

        Returns True when a forward was posted. No-ops (return False) when the
        target channel is unset, the mention is already in the support channel,
        it's a DM, or the sender is the bot itself.
        """
        if not self._support_channel_id:
            logger.warning(
                "CUSTOMERBOT_USERLED_SUPPORT_CHANNEL_ID unset — @UserledSupport "
                "mention forwarding inactive."
            )
            return False
        if channel_id == self._support_channel_id:
            # A mention inside the support channel itself — nothing to route.
            return False
        if channel_id.startswith("D"):
            # DMs to the bot aren't team-visible requests; skip them.
            return False
        if self._bot_user_id is not None and sender_user_id == self._bot_user_id:
            return False

        permalink = self._slack.build_thread_link(channel_id, message_ts)
        blocks = _forward_blocks(
            sender_user_id=sender_user_id,
            channel_id=channel_id,
            text=text,
            permalink=permalink,
        )
        await self._slack.send_blocks(
            self._support_channel_id,
            blocks,
            text=f"UserledSupport mentioned in #{channel_id}",
        )
        return True


def _forward_blocks(
    *,
    sender_user_id: str,
    channel_id: str,
    text: str,
    permalink: str,
) -> list[dict[str, Any]]:
    """Block Kit for the forwarded post: who + where, the message, a thread link."""
    header = f":inbox_tray: <@{sender_user_id}> mentioned *UserledSupport* in <#{channel_id}>"
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
    ]
    snippet = text.strip()
    if snippet:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f">>> {snippet}"}})
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"<{permalink}|Open thread →>"}],
        }
    )
    return blocks
