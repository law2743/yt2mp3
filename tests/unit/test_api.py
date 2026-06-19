from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


def make_settings(tmp_path: Path) -> Settings:
    return Settings(app_env="test", work_root=tmp_path)


def test_health_does_not_expose_paths(tmp_path):
    app.dependency_overrides[get_settings] = lambda: make_settings(tmp_path)
    with TestClient(app) as client:
        response = client.get("/health")
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert str(tmp_path) not in response.text


def test_invalid_url_uses_public_error_shape(tmp_path):
    app.dependency_overrides[get_settings] = lambda: make_settings(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/jobs/analyze", json={"url": "https://evil.test/video"})
    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_YOUTUBE_URL"


def test_oversized_body_is_rejected(tmp_path):
    app.dependency_overrides[get_settings] = lambda: make_settings(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs/analyze",
            content=b"x" * (16 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_invalid_schema_has_uniform_error(tmp_path):
    app.dependency_overrides[get_settings] = lambda: make_settings(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/jobs/analyze", json={"url": 123})
    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert set(response.json()["error"]) == {"code", "message", "retryable"}
