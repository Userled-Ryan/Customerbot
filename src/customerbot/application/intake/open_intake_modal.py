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
from customerbot.domain.tickets.value_objects import Source

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
        product_channel_id: str | None = None,
        gleap_channel_id: str | None = None,
    ) -> None:
        self._slack = slack
        self._orgs = orgs
        self._drafts = drafts
        self._tech_assistance_channel_id = tech_assistance_channel_id
        self._product_channel_id = product_channel_id
        self._gleap_channel_id = gleap_channel_id
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
            # Pre-select the org when the invoking channel is a customer's org
            # channel, so logging from a customer channel is one field lighter.
            initial_org_id: str | None = None
            if invoker_channel_id:
                initial_org_id = next(
                    (o.id for o in orgs if o.slack_channel_id == invoker_channel_id), None
                )
            view = self._se_view_builder(
                orgs,
                private_metadata=private_metadata,
                prefill_description=prefill_description,
                initial_source=self._initial_source(invoker_channel_id),
                initial_org_id=initial_org_id,
                # Default the "create new org" owner picker to whoever is
                # logging, so CS usually only has to paste the channel id.
                initial_owner_id=invoker_user_id,
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

    async def toggle_new_org(
        self,
        *,
        view_id: str,
        show_new_org: bool,
        invoker_user_id: str,
        state_values: dict[str, Any],
        private_metadata: str,
    ) -> None:
        """Re-render the open SE intake modal to show/hide the inline new-org
        fields (invoked from the Org dropdown's block-action).

        `state_values` (the view's current `state.values`) is threaded back
        into the rebuilt view so whatever the SE already typed survives the
        `views.update`. The owner picker defaults to the SE who's logging.
        """
        orgs = await self._orgs.list_all()
        view = self._se_view_builder(
            orgs,
            private_metadata=private_metadata,
            show_new_org=show_new_org,
            state_values=state_values,
            initial_owner_id=invoker_user_id,
        )
        await self._slack.update_view(view_id=view_id, view=view)

    def _choose_modal(self, invoker_channel_id: str | None) -> ModalKind:
        """Always open the full SE intake form.

        We retired the per-channel split: `#userled-support` (formerly
        `#tech-assistance`) used to open a simplified CSM form. Customers now
        just post free text / screenshots in the channel and the SE logs the
        ticket via the full form, so there's a single intake everywhere.
        `invoker_channel_id` is kept for signature stability / easy revert.
        """
        return ModalKind.SE_BUG

    def _initial_source(self, invoker_channel_id: str | None) -> Source:
        """Pre-select the Source dropdown to match where `/log` was invoked, so
        the SE rarely has to change it.

        - DM (channel id starts with `D`) → DM
        - the support channel → `#userled-support`
        - the Gleap channel → In-app
        - the #product channel → `#product`
        - a customer org's channel → Customer channel
        - anything else (unknown channel / no context) → Customer channel, since
          the form is now used from customer channels by default.
        """
        if invoker_channel_id is None:
            return Source.DM
        if invoker_channel_id.startswith("D"):
            return Source.DM
        if (
            self._tech_assistance_channel_id
            and invoker_channel_id == self._tech_assistance_channel_id
        ):
            return Source.TECH_ASSISTANCE
        if self._gleap_channel_id and invoker_channel_id == self._gleap_channel_id:
            return Source.IN_APP
        if self._product_channel_id and invoker_channel_id == self._product_channel_id:
            return Source.PRODUCT_CHANNEL
        return Source.CUSTOMER_CHANNEL
