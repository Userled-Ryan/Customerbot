"""OpenIntakeModal — invoked from the `/log` (and `/l`) slash command.

Decides which modal to open based on the invoking channel (§3b vs §3c), opens
the view via the Slack port, and persists a `draft_form_sessions` row so the
sweeper can drop it if SE doesn't submit within 30 min (§3a).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from customerbot.domain.bot_state.entities import DraftFormSession, ModalKind
from customerbot.domain.bot_state.ports import DraftFormSessionRepositoryPort
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.ports import OrgRepositoryPort

ViewBuilder = Callable[..., dict[str, Any]]
"""Callable taking `orgs` (and optional keyword args) → Block-Kit view dict.

Concrete implementations live in `integration/slack/modals/{csm_intake,se_bug}.py`
and are injected at app-construction time so this use case stays in the
application layer.
"""

logger = logging.getLogger(__name__)

DRAFT_TTL = timedelta(minutes=30)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OpenIntakeModal:
    def __init__(
        self,
        slack: SlackPort,
        orgs: OrgRepositoryPort,
        drafts: DraftFormSessionRepositoryPort,
        tech_assistance_channel_id: str | None,
        csm_view_builder: ViewBuilder,
        se_view_builder: ViewBuilder,
    ) -> None:
        self._slack = slack
        self._orgs = orgs
        self._drafts = drafts
        self._tech_assistance_channel_id = tech_assistance_channel_id
        self._csm_view_builder = csm_view_builder
        self._se_view_builder = se_view_builder

    async def execute(
        self,
        *,
        trigger_id: str,
        invoker_user_id: str,
        invoker_channel_id: str | None,
        invoker_thread_ts: str | None = None,
        prefill_description: str = "",
        original_slack_link: str | None = None,
    ) -> str | None:
        modal_kind = self._choose_modal(invoker_channel_id)
        orgs = await self._orgs.list_all()
        # `private_metadata` round-trips through view_submission so the eventual
        # ticket can be linked back to the original Slack thread (§3a).
        private_metadata = original_slack_link or ""

        if modal_kind == ModalKind.CSM_INTAKE:
            view = self._csm_view_builder(orgs, private_metadata=private_metadata)
        else:
            view = self._se_view_builder(
                orgs,
                private_metadata=private_metadata,
                prefill_description=prefill_description,
            )

        view_id = await self._slack.open_view(trigger_id=trigger_id, view=view)
        if view_id is None:
            logger.warning("Slack rejected views.open; no draft recorded")
            return None

        now = _utcnow()
        await self._drafts.create(
            DraftFormSession(
                slack_view_id=view_id,
                modal_kind=modal_kind,
                invoker_user_id=invoker_user_id,
                invoker_channel_id=invoker_channel_id,
                invoker_thread_ts=invoker_thread_ts,
                payload_json="{}",
                created_at=now,
                expires_at=now + DRAFT_TTL,
            )
        )
        logger.info(
            "Opened %s modal for %s in channel %s (view_id=%s)",
            modal_kind.value,
            invoker_user_id,
            invoker_channel_id,
            view_id,
        )
        return view_id

    def _choose_modal(self, invoker_channel_id: str | None) -> ModalKind:
        """Per §3b/§3c: `#tech-assistance` → CSM intake; everywhere else → SE bug."""
        if (
            self._tech_assistance_channel_id
            and invoker_channel_id == self._tech_assistance_channel_id
        ):
            return ModalKind.CSM_INTAKE
        return ModalKind.SE_BUG
