"""APEX backend entry point.

Builds the FastAPI application. Domain routers are mounted under the versioned
API prefix as each bounded context lands; ``/health`` stays at the root so
infrastructure can probe it without knowing the API version.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the APEX FastAPI application."""
    settings = settings or get_settings()

    app = FastAPI(
        title="APEX",
        description="Enterprise knowledge, governance and intelligence platform.",
        version=__version__,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)

    return app


app = create_app()
