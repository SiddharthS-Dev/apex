"""Shared test fixtures.

Database-backed tests run against a real PostgreSQL instance rather than
SQLite. Tenant isolation depends on SQLAlchemy ORM event behaviour and on
PostgreSQL types (``uuid``, ``timestamptz``); verifying it against a different
dialect would prove something other than what ships.

Point ``APEX_TEST_DATABASE_URL`` at a **disposable** database -- the schema is
dropped and recreated per test. It must not be the database Alembic manages,
or the migration state and the actual tables will disagree. When no database is
reachable, these tests skip rather than fail, so the suite stays runnable
without Docker.

API tests drive the app through ``httpx.ASGITransport`` rather than
``TestClient``, so the request runs on the same event loop as the test. A
database connection is bound to the loop that created it; a client that runs
the app on its own loop cannot share the test's session.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import NullPool

# Importing the registry registers every model with Base.metadata.
from app.core import registry  # noqa: F401
from app.core.models import Base, GlobalBase, TenantScopedBase

# Configure the event loop policy before pytest-asyncio creates a loop.
# This otherwise happens as a side effect of importing app.core.database, which
# makes it depend on which test modules were collected -- running one file in
# isolation would fail to connect while the full suite succeeded.
from app.core.runtime import configure_event_loop_policy  # noqa: E402

configure_event_loop_policy()

TEST_DATABASE_URL = os.environ.get(
    "APEX_TEST_DATABASE_URL",
    "postgresql+psycopg://apex:apex@localhost:5432/apex_test",
)

#: Mirrors the trigger created by the audit migration. Kept here rather than
#: imported from the migration because a migration is a historical record and
#: must not become a runtime dependency; if the two drift,
#: ``test_trigger_matches_the_migration`` fails.
AUDIT_IMMUTABILITY_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION apex_audit_event_immutable()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_event is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_event_immutable ON audit_event;

CREATE TRIGGER audit_event_immutable
BEFORE UPDATE OR DELETE ON audit_event
FOR EACH ROW EXECUTE FUNCTION apex_audit_event_immutable();
"""


class TenantWidget(TenantScopedBase):
    """Throwaway tenant-scoped model, defined only for the test suite."""

    __tablename__ = "_test_tenant_widget"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


class GlobalWidget(GlobalBase):
    """Throwaway platform-level model with no tenant discriminator."""

    __tablename__ = "_test_global_widget"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """An engine against a freshly built test schema, or skip if unreachable."""
    candidate = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with candidate.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- any failure means "no database"
        await candidate.dispose()
        pytest.skip(f"No test database at {TEST_DATABASE_URL}: {type(exc).__name__}")

    async with candidate.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
        # create_all builds tables from metadata, which knows nothing about
        # triggers. Install the audit append-only trigger the migration
        # creates, so tests exercise the real database-level guarantee rather
        # than only the application guard in front of it.
        await connection.execute(text(AUDIT_IMMUTABILITY_TRIGGER_SQL))

    yield candidate

    async with candidate.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await candidate.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to the test database."""
    async with AsyncSession(engine, expire_on_commit=False) as db_session:
        yield db_session


@pytest_asyncio.fixture
async def app(session: AsyncSession, engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    """The application, with its database dependency bound to the test session."""
    from app.core.database import get_session_factory
    from app.identity.dependencies import db_session as db_session_dependency
    from app.main import create_app

    application = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def _override_factory() -> async_sessionmaker[AsyncSession]:
        # Audit writes that must outlive a failing request open their own
        # session, so they need the test engine rather than the configured one.
        return async_sessionmaker(engine, expire_on_commit=False)

    application.dependency_overrides[db_session_dependency] = _override_session
    application.dependency_overrides[get_session_factory] = _override_factory
    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An HTTP client driving the app in this test's event loop."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://apex.test") as http_client:
        yield http_client


@pytest.fixture
def tenant_a() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")


@pytest.fixture
def tenant_b() -> uuid.UUID:
    return uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
