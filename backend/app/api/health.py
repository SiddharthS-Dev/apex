"""Liveness and readiness endpoints.

``/health`` is deliberately unauthenticated and dependency-free so that Docker,
CI smoke tests and load balancers can probe the process itself. Checks for
downstream dependencies (database, object storage) are added by the commits
that introduce them.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Payload returned by the health probe."""

    status: str
    service: str
    version: str
    environment: str


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
