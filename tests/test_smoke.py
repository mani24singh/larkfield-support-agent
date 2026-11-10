"""Smoke tests: the app imports, builds and answers the liveness probe."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.solution import app


def test_health_returns_ok() -> None:
    """GET /health must return 200 and the contract body."""
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_factory_uses_given_settings() -> None:
    """The factory is importable and accepts explicit settings."""
    settings = Settings(port=8123)
    client = TestClient(create_app(settings))

    assert client.get("/health").status_code == 200
    assert client.app.state.settings.port == 8123


def test_unknown_route_is_404() -> None:
    """Routes outside the contract are not exposed."""
    client = TestClient(app)

    assert client.get("/nope").status_code == 404
