from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI


class IntegrationHandler(Protocol):
    """Protocol that each messaging integration must implement."""

    @property
    def integration_id(self) -> str: ...

    def register_routes(self, app: FastAPI) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
