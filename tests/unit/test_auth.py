from pathlib import Path

import pytest

from app.api.auth import login_attempts
from app.config import Settings
from tests.unit.api_client import api_client


def production_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="production",
        app_password="correct horse battery staple",
        token_secret="test-secret-that-is-at-least-32-characters",
        cors_allowed_origins="https://frontend.example",
        work_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_login_issues_bearer_token_and_session_accepts_it(tmp_path):
    settings = production_settings(tmp_path)
    login_attempts.failures.clear()
    try:
        async with api_client(settings) as client:
            login = await client.post(
                "/api/auth/login",
                json={"password": "correct horse battery staple"},
            )
            assert login.status_code == 200
            body = login.json()
            assert body["token_type"] == "bearer"
            session = await client.get(
                "/api/auth/session",
                headers={"Authorization": f"Bearer {body['access_token']}"},
            )
            assert session.status_code == 200
            assert session.json()["authenticated"] is True
    finally:
        login_attempts.failures.clear()


@pytest.mark.asyncio
async def test_wrong_password_and_tampered_token_are_rejected(tmp_path):
    settings = production_settings(tmp_path)
    login_attempts.failures.clear()
    try:
        async with api_client(settings) as client:
            wrong = await client.post("/api/auth/login", json={"password": "wrong"})
            assert wrong.status_code == 401
            assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
            session = await client.get(
                "/api/auth/session",
                headers={"Authorization": "Bearer definitely-not-a-token"},
            )
            assert session.status_code == 401
            assert session.json()["error"]["code"] == "INVALID_TOKEN"
    finally:
        login_attempts.failures.clear()


@pytest.mark.asyncio
async def test_production_jobs_require_authorization(tmp_path):
    settings = production_settings(tmp_path)
    async with api_client(settings) as client:
        response = await client.post(
            "/api/jobs/analyze",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_repeated_login_failures_are_rate_limited(tmp_path):
    settings = production_settings(tmp_path).model_copy(
        update={"login_max_failures": 2, "login_failure_window_seconds": 60},
    )
    login_attempts.failures.clear()
    try:
        async with api_client(settings) as client:
            assert (
                await client.post("/api/auth/login", json={"password": "wrong"})
            ).status_code == 401
            assert (
                await client.post("/api/auth/login", json={"password": "wrong"})
            ).status_code == 401
            limited = await client.post("/api/auth/login", json={"password": "wrong"})
            assert limited.status_code == 429
            assert limited.json()["error"]["code"] == "LOGIN_RATE_LIMITED"
    finally:
        login_attempts.failures.clear()
