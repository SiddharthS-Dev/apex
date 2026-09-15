"""Declarative base and the mixins every APEX table is built from.

Three mixins compose into the shapes the platform needs:

``UUIDPrimaryKey``
    Surrogate UUID keys. Generated application-side so an entity has an
    identity before it is flushed -- provenance and audit both need to
    reference a row that has not been written yet.

``Timestamped``
    ``created_at`` / ``updated_at``, both server-side so the database clock
    is the single source of truth regardless of which process wrote the row.

``TenantScoped``
    The mandatory ``tenant_id`` discriminator. Isolation is enforced in
    :mod:`app.core.tenancy`, not here -- this mixin only declares the column
    and the index that makes filtering on it cheap.

Not every table is tenant-scoped. Platform-level tables (the tenant registry
itself, system configuration) are global by nature, which is why tenancy is a
mixin rather than part of ``Base``.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint naming. Without this, PostgreSQL invents names, Alembic
# autogenerate produces unstable diffs, and a migration cannot reliably drop a
# constraint it did not create.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every APEX model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    """Surrogate UUID primary key, generated application-side."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class Timestamped:
    """Creation and last-modification timestamps, both server-generated."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantScoped:
    """Mandatory tenant discriminator for tenant-owned rows.

    Declaring this mixin is what opts a model into the isolation enforced by
    :mod:`app.core.tenancy`: reads are filtered to the active tenant and writes
    are stamped and guarded. A model that omits it is global by definition.
    """

    # Indexed because every tenant-scoped query filters on it. Declared on the
    # column rather than in ``__table_args__`` so that a subclass defining its
    # own table args does not silently drop the index.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )


class TenantScopedBase(Base, UUIDPrimaryKey, Timestamped, TenantScoped):
    """Convenience base for the common case: tenant-owned, keyed, timestamped."""

    __abstract__ = True


class GlobalBase(Base, UUIDPrimaryKey, Timestamped):
    """Convenience base for platform-level tables that no tenant owns."""

    __abstract__ = True
