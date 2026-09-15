"""Tenant isolation tests.

These assert the guarantee behind assumption **A-07**: with shared-schema
multi-tenancy, cross-tenant access must be prevented by default rather than by
remembering to filter.

Every test here uses a plain ``select()`` with no tenant predicate of its own.
That is the point -- the isolation must come from the session guards, not from
the query.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import (
    CrossTenantAccessError,
    TenantContextMissingError,
    current_tenant,
    current_tenant_or_none,
    system_scope,
    tenant_scope,
)
from tests.conftest import GlobalWidget, TenantWidget


async def _seed(session: AsyncSession, tenant: uuid.UUID, name: str) -> uuid.UUID:
    """Insert one widget for a tenant and return its id."""
    with tenant_scope(tenant):
        widget = TenantWidget(name=name)
        session.add(widget)
        await session.commit()
        return widget.id


# --- Context management -------------------------------------------------


def test_no_tenant_is_active_by_default() -> None:
    assert current_tenant_or_none() is None


def test_current_tenant_raises_when_unset() -> None:
    with pytest.raises(TenantContextMissingError):
        current_tenant()


def test_tenant_scope_is_restored_on_exit(tenant_a: uuid.UUID) -> None:
    with tenant_scope(tenant_a):
        assert current_tenant() == tenant_a
    assert current_tenant_or_none() is None


def test_tenant_scope_nests(tenant_a: uuid.UUID, tenant_b: uuid.UUID) -> None:
    with tenant_scope(tenant_a):
        with tenant_scope(tenant_b):
            assert current_tenant() == tenant_b
        assert current_tenant() == tenant_a


# --- Reads --------------------------------------------------------------


async def test_read_without_tenant_scope_is_refused(session: AsyncSession) -> None:
    """Default-deny: an unscoped read is refused, not silently widened."""
    with pytest.raises(TenantContextMissingError):
        await session.execute(select(TenantWidget))


async def test_read_is_filtered_to_the_active_tenant(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _seed(session, tenant_a, "alpha")
    await _seed(session, tenant_b, "beta")

    with tenant_scope(tenant_a):
        rows = (await session.execute(select(TenantWidget))).scalars().all()

    assert [row.name for row in rows] == ["alpha"]


async def test_other_tenants_rows_are_invisible_even_by_id(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Knowing the primary key must not be enough to read another tenant's row."""
    widget_id = await _seed(session, tenant_b, "beta")

    with tenant_scope(tenant_a):
        session.expunge_all()
        found = (
            await session.execute(select(TenantWidget).where(TenantWidget.id == widget_id))
        ).scalar_one_or_none()

    assert found is None


async def test_global_models_need_no_tenant_scope(session: AsyncSession) -> None:
    """A model without the mixin is unaffected by tenant filtering."""
    session.add(GlobalWidget(name="platform"))
    await session.commit()

    rows = (await session.execute(select(GlobalWidget))).scalars().all()

    assert [row.name for row in rows] == ["platform"]


# --- Writes -------------------------------------------------------------


async def test_insert_is_stamped_with_the_active_tenant(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    widget = TenantWidget(name="alpha")
    with tenant_scope(tenant_a):
        session.add(widget)
        await session.commit()

    assert widget.tenant_id == tenant_a


async def test_insert_without_tenant_scope_is_refused(session: AsyncSession) -> None:
    session.add(TenantWidget(name="orphan"))

    with pytest.raises(TenantContextMissingError):
        await session.commit()

    await session.rollback()


async def test_insert_for_another_tenant_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """An explicit tenant_id cannot override the active scope."""
    with tenant_scope(tenant_a):
        session.add(TenantWidget(name="smuggled", tenant_id=tenant_b))
        with pytest.raises(CrossTenantAccessError):
            await session.commit()

    await session.rollback()


async def test_update_of_another_tenants_row_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _seed(session, tenant_b, "beta")

    with system_scope():
        widget = (await session.execute(select(TenantWidget))).scalars().one()

    widget.name = "tampered"
    with tenant_scope(tenant_a):
        with pytest.raises(CrossTenantAccessError):
            await session.commit()

    await session.rollback()


async def test_delete_of_another_tenants_row_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _seed(session, tenant_b, "beta")

    with system_scope():
        widget = (await session.execute(select(TenantWidget))).scalars().one()

    with tenant_scope(tenant_a):
        await session.delete(widget)
        with pytest.raises(CrossTenantAccessError):
            await session.commit()

    await session.rollback()


# --- System scope -------------------------------------------------------


async def test_system_scope_sees_every_tenant(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _seed(session, tenant_a, "alpha")
    await _seed(session, tenant_b, "beta")

    with system_scope():
        rows = (await session.execute(select(TenantWidget))).scalars().all()

    assert sorted(row.name for row in rows) == ["alpha", "beta"]


async def test_system_scope_does_not_leak_past_the_block(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await _seed(session, tenant_a, "alpha")

    with system_scope():
        pass

    with pytest.raises(TenantContextMissingError):
        await session.execute(select(TenantWidget))
