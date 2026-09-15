"""Tests for the liveness and readiness probes."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


# --- Liveness -----------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_identifies_the_service_and_version(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["service"] == "apex-backend"
    assert body["version"] == __version__
    assert body["environment"]


def test_liveness_does_not_touch_the_database(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database outage must not cause healthy containers to be restarted."""

    async def _explode() -> bool:
        raise AssertionError("liveness must not check the database")

    monkeypatch.setattr("app.api.health.check_database", _explode)

    assert client.get("/health").status_code == 200


# --- Readiness ----------------------------------------------------------


def test_readiness_reports_ready_when_the_database_answers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _ok() -> bool:
        return True

    monkeypatch.setattr("app.api.health.check_database", _ok)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["checks"]["database"] == "ok"


def test_readiness_returns_503_when_the_database_is_unavailable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 takes the instance out of rotation without restarting it."""

    async def _down() -> bool:
        return False

    monkeypatch.setattr("app.api.health.check_database", _down)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database"] == "unavailable"
