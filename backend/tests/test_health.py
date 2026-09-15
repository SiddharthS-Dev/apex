"""Tests for the liveness probe."""

from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app

client = TestClient(create_app())


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_identifies_the_service_and_version() -> None:
    body = client.get("/health").json()

    assert body["service"] == "apex-backend"
    assert body["version"] == __version__
    assert body["environment"]
