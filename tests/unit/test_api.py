from pathlib import Path

import pytest

from app.config import Settings
from tests.unit.api_client import api_client


def make_settings(tmp_path: Path) -> Settings:
    return Settings(app_env="test", app_password=None, work_root=tmp_path)


@pytest.mark.asyncio
async def test_health_does_not_expose_paths(tmp_path):
    async with api_client(make_settings(tmp_path)) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert str(tmp_path) not in response.text


@pytest.mark.asyncio
async def test_invalid_url_uses_public_error_shape(tmp_path):
    async with api_client(make_settings(tmp_path)) as client:
        response = await client.post("/api/jobs/analyze", json={"url": "https://evil.test/video"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_YOUTUBE_URL"


@pytest.mark.asyncio
async def test_oversized_body_is_rejected(tmp_path):
    async with api_client(make_settings(tmp_path)) as client:
        response = await client.post(
            "/api/jobs/analyze",
            content=b"x" * (16 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.asyncio
async def test_cors_preflight_allows_configured_private_network_frontend(tmp_path):
    async with api_client(make_settings(tmp_path)) as client:
        response = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5500",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5500"
    assert response.headers["access-control-allow-private-network"] == "true"


@pytest.mark.asyncio
async def test_invalid_schema_has_uniform_error(tmp_path):
    async with api_client(make_settings(tmp_path)) as client:
        response = await client.post("/api/jobs/analyze", json={"url": 123})
    assert response.status_code == 400
    assert set(response.json()["error"]) == {"code", "message", "retryable"}
