"""Async engine, session factory and the FastAPI session dependency.

The engine is created lazily and held per process so that importing this module
does not open connections -- tests, Alembic and the CLI all import it without
necessarily needing a database.

Importing this module has two deliberate side effects:

- it installs the tenant isolation guards from :mod:`app.core.tenancy`, so any
  code path that can obtain a session has isolation enforced -- there is no way
  to get an unguarded session;
- it configures the event loop policy for the platform, which must happen
  before a loop exists (see :mod:`app.core.runtime`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.runtime import configure_event_loop_policy
from app.core.tenancy import install_tenant_guards

configure_event_loop_policy()
install_tenant_guards()

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine_for_url(url: str, settings: Settings | None = None) -> AsyncEngine:
    """Build an async engine for an explicit URL.

    Separated from :func:`create_engine` so the region registry in
    :mod:`app.platform.regions` can build one engine per region without
    duplicating pool configuration.
    """
    settings = settings or get_settings()
    return create_async_engine(
        url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        # Verify a connection before handing it out; Postgres restarts and
        # idle-connection reapers would otherwise surface as request errors.
        pool_pre_ping=True,
    )


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build a new async engine for the default database."""
    settings = settings or get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        # Verify a connection before handing it out; Postgres restarts and
        # idle-connection reapers would otherwise surface as request errors.
        pool_pre_ping=True,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session bound to the request.

    The session is rolled back and closed when the request ends. Committing is
    the caller's decision -- an endpoint that only reads should never leave an
    open transaction behind.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_database() -> bool:
    """Return whether the database answers a trivial query.

    Uses Core SQL rather than the ORM, so it needs no tenant scope.
    """
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 -- a readiness probe reports, never raises
        return False
    return True


async def dispose_engine() -> None:
    """Close the engine's connection pool, for shutdown and tests."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
