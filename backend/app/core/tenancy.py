"""Tenant isolation for the shared-schema multi-tenancy model.

APEX runs logical multi-tenancy: one PostgreSQL schema, a mandatory
``tenant_id`` discriminator on tenant-owned tables, and isolation enforced
here at the data-access layer (assumption **A-07**).

The design goal is that isolation is **not** something a developer can forget.
Rather than relying on every query adding ``WHERE tenant_id = ...``, two
SQLAlchemy session events enforce it for all of them:

``do_orm_execute``
    Every ORM SELECT touching a :class:`~app.core.models.TenantScoped` entity
    is rewritten to filter on the active tenant. With no active tenant the
    query is **refused**, not silently widened -- default-deny.

``before_flush``
    New tenant-scoped rows are stamped with the active tenant. Any attempt to
    write or delete a row belonging to a different tenant raises.

The escape hatch is :func:`system_scope`, which is deliberately explicit and
greppable. Migrations and genuinely cross-tenant platform operations use it;
request handling never should.

This is the mechanism behind
:doc:`ADR-0004 <../../../docs/adr/0004-authorization-at-retrieval>` at the
tenancy level. ABAC scope (audience, geography, classification) layers on top
in Commit 005.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.core.models import TenantScoped

_current_tenant: ContextVar[uuid.UUID | None] = ContextVar(
    "apex_current_tenant",
    default=None,
)
_system_scope: ContextVar[bool] = ContextVar("apex_system_scope", default=False)

#: Execution option that skips the tenant filter for a single statement.
#: Prefer :func:`system_scope` -- this exists for infrastructure queries such
#: as health probes that touch no tenant-scoped entity.
SKIP_TENANT_FILTER = "apex_skip_tenant_filter"


class TenantIsolationError(RuntimeError):
    """Base class for tenant isolation failures."""


class TenantContextMissingError(TenantIsolationError):
    """A tenant-scoped operation was attempted with no active tenant."""


class CrossTenantAccessError(TenantIsolationError):
    """A write or delete targeted a row owned by a different tenant."""


def current_tenant() -> uuid.UUID:
    """Return the active tenant, raising if none is set."""
    tenant = _current_tenant.get()
    if tenant is None:
        raise TenantContextMissingError(
            "No active tenant. Tenant-scoped access requires tenant_scope(); "
            "use system_scope() only for deliberate platform-level operations."
        )
    return tenant


def current_tenant_or_none() -> uuid.UUID | None:
    """Return the active tenant, or ``None`` when unset."""
    return _current_tenant.get()


def in_system_scope() -> bool:
    """Whether tenant filtering is currently suspended."""
    return _system_scope.get()


@contextmanager
def tenant_scope(tenant_id: uuid.UUID) -> Iterator[uuid.UUID]:
    """Bind a tenant for the duration of the block.

    Every ORM read inside is filtered to ``tenant_id`` and every write is
    stamped with it.
    """
    token: Token[uuid.UUID | None] = _current_tenant.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _current_tenant.reset(token)


@contextmanager
def system_scope() -> Iterator[None]:
    """Suspend tenant filtering for a deliberate platform-level operation.

    Used by migrations and cross-tenant maintenance. Never use this to serve a
    user request -- doing so disables the isolation guarantee entirely.
    """
    token: Token[bool] = _system_scope.set(True)
    try:
        yield
    finally:
        _system_scope.reset(token)


def _touches_tenant_scoped(state: ORMExecuteState) -> bool:
    """Whether this statement involves any tenant-scoped entity."""
    return any(
        isinstance(mapper.class_, type) and issubclass(mapper.class_, TenantScoped)
        for mapper in state.all_mappers
    )


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state: ORMExecuteState) -> None:
    """Filter every ORM SELECT to the active tenant, or refuse it."""
    if not state.is_select:
        return
    # Column and relationship loads inherit the criteria of the query that
    # triggered them; re-applying would double-filter aliased targets.
    if state.is_column_load or state.is_relationship_load:
        return
    if state.execution_options.get(SKIP_TENANT_FILTER, False):
        return
    if in_system_scope():
        return
    if not _touches_tenant_scoped(state):
        return

    tenant = _current_tenant.get()
    if tenant is None:
        raise TenantContextMissingError(
            "Refusing an unscoped read of tenant-owned data. "
            "Wrap the operation in tenant_scope(), or system_scope() if it is "
            "genuinely a platform-level operation."
        )

    # Closure variables in this lambda are tracked by SQLAlchemy's lambda
    # statement system and extracted as bound parameters, so the per-tenant
    # value is not baked into the compiled-statement cache.
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == tenant,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_and_guard_writes(
    session: Session,
    flush_context: Any,
    instances: Any,
) -> None:
    """Stamp new tenant-scoped rows and refuse cross-tenant writes."""
    if in_system_scope():
        return

    tenant = _current_tenant.get()

    for obj in session.new:
        if not isinstance(obj, TenantScoped):
            continue
        existing = getattr(obj, "tenant_id", None)
        if existing is None:
            if tenant is None:
                raise TenantContextMissingError(
                    f"Cannot insert {type(obj).__name__} without an active tenant."
                )
            obj.tenant_id = tenant
        elif tenant is not None and existing != tenant:
            raise CrossTenantAccessError(
                f"Refusing to insert {type(obj).__name__} for tenant {existing} "
                f"while tenant {tenant} is active."
            )

    for obj in (*session.dirty, *session.deleted):
        if not isinstance(obj, TenantScoped):
            continue
        owner = getattr(obj, "tenant_id", None)
        if tenant is None:
            raise TenantContextMissingError(
                f"Cannot modify {type(obj).__name__} without an active tenant."
            )
        if owner is not None and owner != tenant:
            raise CrossTenantAccessError(
                f"Refusing to modify {type(obj).__name__} owned by tenant {owner} "
                f"while tenant {tenant} is active."
            )


def install_tenant_guards() -> None:
    """Ensure the isolation listeners are registered.

    Registration happens at import time; this function exists so callers can
    make the dependency explicit and assert it in tests.
    """
    if not event.contains(Session, "do_orm_execute", _apply_tenant_filter):
        event.listen(Session, "do_orm_execute", _apply_tenant_filter)
    if not event.contains(Session, "before_flush", _stamp_and_guard_writes):
        event.listen(Session, "before_flush", _stamp_and_guard_writes)
