from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI

from prbot.domain.tracking.ports import ReactionPort
from prbot.domain.tracking.value_objects import MessageRef


class IntegrationHandler(Protocol):
    """Protocol that each messaging integration must implement."""

    @property
    def integration_id(self) -> str:
        """Unique identifier, e.g. 'slack', 'discord'."""
        ...

    def reaction_port(self) -> ReactionPort:
        """Return the adapter that can add reactions for this integration."""
        ...

    def register_routes(self, app: FastAPI) -> None:
        """Register any HTTP routes this integration needs (webhooks, events, etc.)."""
        ...

    async def start(self) -> None:
        """Lifecycle hook: called on app startup."""
        ...

    async def stop(self) -> None:
        """Lifecycle hook: called on app shutdown."""
        ...


class IntegrationRegistry:
    """Routes reaction calls to the correct integration based on message_ref.integration_id.

    Implements ReactionPort so it can be passed directly to use cases.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, IntegrationHandler] = {}

    def register(self, handler: IntegrationHandler) -> None:
        self._handlers[handler.integration_id] = handler

    async def add_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        handler = self._handlers.get(message_ref.integration_id)
        if handler is None:
            raise ValueError(f"No integration registered for '{message_ref.integration_id}'")
        await handler.reaction_port().add_reaction(message_ref, emoji)

    def register_all_routes(self, app: FastAPI) -> None:
        for handler in self._handlers.values():
            handler.register_routes(app)

    async def start_all(self) -> None:
        for handler in self._handlers.values():
            await handler.start()

    async def stop_all(self) -> None:
        for handler in self._handlers.values():
            await handler.stop()
