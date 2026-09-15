"""APEX backend entry point.

Builds the FastAPI application. Domain routers are mounted under the versioned
API prefix as each bounded context lands; ``/health`` stays at the root so
infrastructure can probe it without knowing the API version.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.audit.router import router as audit_router
from app.core.config import Settings, get_settings
from app.core.correlation import CorrelationIdMiddleware
from app.core.database import dispose_engine, get_session_factory
from app.identity.router import auth_router, identity_router
from app.identity.service import sync_permissions
from app.platform.regions import dispose_region_engines

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Synchronise the permission vocabulary, then release the pool on exit.

    Permissions are defined in code and mirrored into the database, so a deploy
    that adds an enforced permission makes it grantable without a migration.
    The sync is idempotent and global, so it needs no tenant context.

    A database that is unreachable at startup is not fatal: the readiness probe
    already reports that state, and refusing to start would turn a transient
    outage into a restart loop.

    On shutdown **every** connection pool is disposed -- the default engine and
    each regional engine. Missing either leaks connections across restarts.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            written = await sync_permissions(session)
        if written:
            logger.info("Synchronised %d permission definitions", written)
    except Exception:
        # Not fatal: /health/ready already reports an unreachable database, and
        # refusing to start would turn a transient outage into a restart loop.
        logger.exception("Could not synchronise permissions at startup")

    yield

    # Both pools, not just the default one. P02 introduced a second engine
    # registry keyed by region; disposing only the default left every regional
    # pool open, so connections survived shutdown and accumulated across
    # restarts until PostgreSQL refused new ones.
    await dispose_engine()
    await dispose_region_engines()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the APEX FastAPI application."""
    settings = settings or get_settings()

    app = FastAPI(
        title="APEX",
        description="Enterprise knowledge, governance and intelligence platform.",
        version=__version__,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Outermost, so every request -- including one rejected by auth -- carries a
    # correlation id that its audit records can be tied to.
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health.router)
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(identity_router, prefix=settings.api_prefix)
    app.include_router(audit_router, prefix=settings.api_prefix)

    return app


app = create_app()
