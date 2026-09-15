"""Liveness and readiness endpoints.

The two are deliberately separate, because an orchestrator must be able to tell
them apart:

``/health`` (liveness)
    Is the process alive? Dependency-free, so a database outage never causes
    healthy API containers to be killed and restarted in a loop.

``/health/ready`` (readiness)
    Can this instance serve traffic? Checks the database, and returns 503 when
    it cannot, so the instance is removed from the load balancer without being
    restarted.

Both are unauthenticated: probes run before any credential is available.
"""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app import __version__
from app.core.config import get_settings
from app.core.database import check_database

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Payload returned by the liveness probe."""

    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Payload returned by the readiness probe."""

    status: str
    service: str
    version: str
    environment: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    """Report that the API process is up and serving requests."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service="apex-backend",
        version=__version__,
        environment=settings.env,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def readiness(response: Response) -> ReadinessResponse:
    """Report whether this instance can serve traffic.

    Returns 503 when a required dependency is unavailable, so the instance is
    taken out of rotation rather than restarted.
    """
    settings = get_settings()
    database_ok = await check_database()

    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if database_ok else "not_ready",
        service="apex-backend",
        version=__version__,
        environment=settings.env,
        checks={"database": "ok" if database_ok else "unavailable"},
    )
