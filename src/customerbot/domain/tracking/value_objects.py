from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class MessageRef(BaseModel, frozen=True):
    """Opaque reference to a message in an external messaging platform."""

    integration_id: str
    ref: str
