"""The customer-facing thread copy — pinned, because customers read it.

These strings are the only things the bot ever says in a customer channel, and
they land in the very channels the `log`/`check` detector listens on, so they're
tested both for wording and for not tripping the detector.
"""

from __future__ import annotations

from customerbot.application.intake.detect_log_check import (
    app_mention_triggers,
    match_trigger_word,
)
from customerbot.application.intake.support_threads import (
    RESOLVED_THREAD_REPLY,
    logged_thread_reply,
)
from customerbot.application.tracking.lane_handoff import DEV_HANDOFF_CUSTOMER_REPLY


def test_logged_thread_reply_copy() -> None:
    assert logged_thread_reply("TIC-042") == (
        ":eyes: Thanks — logged as *TIC-042*. The team is taking a look and we'll update you here."
    )


def test_customer_copy_does_not_arm_the_log_check_detector() -> None:
    """A bot reply containing "log"/"check" as a whole word would make the bot
    prompt itself. The `bot_id` guard in the message handler is the real defence;
    this keeps a future copy tweak from relying on it alone."""
    for text in (
        logged_thread_reply("TIC-042"),
        DEV_HANDOFF_CUSTOMER_REPLY,
        RESOLVED_THREAD_REPLY,
    ):
        assert match_trigger_word(text) is None, text
        assert app_mention_triggers(text) is False, text
