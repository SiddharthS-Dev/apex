"""Alembic environment for APEX.

Two things differ from the stock template:

1. The database URL comes from application settings, never from
   ``alembic.ini``. Migrations and the application therefore cannot disagree
   about which database they target, and no credentials are committed.
2. Migrations run on a **synchronous** engine even though the application is
   async. Migrations are a short-lived, strictly sequential batch job with
   nothing to overlap, so async buys nothing -- and it costs portability:
   psycopg's async driver cannot run on Windows' default event loop, which
   would make ``alembic upgrade`` fail for developers on Windows. psycopg
   serves both modes from the same URL, so this needs no second driver.

Migrations are platform-level operations that legitimately span every tenant.
They use Core SQL rather than the ORM, so the tenant-isolation listeners in
:mod:`app.core.tenancy` -- which hook ORM execution -- do not apply.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import Connection, create_engine

from alembic import context

# Importing the registry is what makes autogenerate see the schema: each commit
# that introduces a bounded context registers its models there.
from app.core import registry  # noqa: F401
from app.core.config import get_settings
from app.core.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column type and server-default drift, not just added and
        # dropped tables -- otherwise autogenerate silently misses changes.
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    engine = create_engine(get_settings().database_url, poolclass=None, future=True)
    try:
        with engine.connect() as connection:
            _do_run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
