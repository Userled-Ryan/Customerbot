from __future__ import annotations

from pydantic import BaseModel


class MessageRef(BaseModel, frozen=True):
    """Opaque reference to a message in an external messaging platform."""

    integration_id: str
    ref: str
