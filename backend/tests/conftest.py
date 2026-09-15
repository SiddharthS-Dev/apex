"""Shared test fixtures.

Database-backed tests run against a real PostgreSQL instance rather than
SQLite. Tenant isolation depends on SQLAlchemy ORM event behaviour and on
PostgreSQL types (``uuid``, ``timestamptz``); verifying it against a different
dialect would prove something other than what ships.

Set ``APEX_TEST_DATABASE_URL`` to point at a disposable database. When none is
reachable, the database-backed tests skip rather than fail, so the suite stays
runnable without Docker.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import String, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.core.models import Base, GlobalBase, TenantScopedBase

TEST_DATABASE_URL = os.environ.get(
    "APEX_TEST_DATABASE_URL",
    "postgresql+psycopg://apex:apex@localhost:5432/apex",
)


class TenantWidget(TenantScopedBase):
    """Throwaway tenant-scoped model, defined only for the test suite.

    Deliberately not part of the application schema: Commit 003 introduces the
    database foundation, not domain tables.
    """

    __tablename__ = "_test_tenant_widget"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


class GlobalWidget(GlobalBase):
    """Throwaway platform-level model with no tenant discriminator."""

    __tablename__ = "_test_global_widget"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


TEST_TABLES = [TenantWidget.__table__, GlobalWidget.__table__]


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """An engine against the test database, or skip if unreachable."""
    candidate = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with candidate.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- any failure means "no database"
        await candidate.dispose()
        pytest.skip(f"No test database at {TEST_DATABASE_URL}: {type(exc).__name__}")

    async with candidate.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all, tables=TEST_TABLES)
        await connection.run_sync(Base.metadata.create_all, tables=TEST_TABLES)

    yield candidate

    async with candidate.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all, tables=TEST_TABLES)
    await candidate.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to the test database."""
    async with AsyncSession(engine, expire_on_commit=False) as db_session:
        yield db_session


@pytest.fixture
def tenant_a() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")


@pytest.fixture
def tenant_b() -> uuid.UUID:
    return uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
