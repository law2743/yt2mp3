from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import Settings, get_settings
from app.main import app


@asynccontextmanager
async def api_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async def settings_override() -> Settings:
        return settings

    app.dependency_overrides[get_settings] = settings_override
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client
    finally:
        app.dependency_overrides.clear()
