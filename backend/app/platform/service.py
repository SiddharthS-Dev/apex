"""Tenant, region and jurisdiction administration.

Moved here from :mod:`app.policy.service` in P02. The behaviour of the tenant
registry operations is unchanged; what changed is which context owns them.

Every operation here is **platform-level** and therefore runs under
``system_scope()`` at explicitly commented call sites. That is not a loosening
of isolation: the registry is what *defines* tenants, so it cannot be filtered
by the tenant it is looking for. Tenant-*owned* data remains unreachable from
here as from anywhere else.

Per Master Prompt §32, tenant administrators must not automatically receive
platform authority. No HTTP surface is exposed for these operations: who may
create a tenant or assign a region is a governance question (A-04), answered at
P06.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import system_scope
from app.platform.models import Jurisdiction, Region, ResidencyMode, Tenant

#: Slug of the sentinel tenant that owns unattributable platform events.
PLATFORM_TENANT_SLUG = "__platform__"


class PlatformError(Exception):
    """Base class for platform-administration failures."""


class TenantNotFoundError(PlatformError):
    """No tenant with that identifier or slug."""


class DuplicateTenantError(PlatformError):
    """A tenant with that slug already exists."""


class RegionNotFoundError(PlatformError):
    """No region with that code."""


class InvalidTenantError(PlatformError):
    """A tenant definition could not be accepted."""


# --- Regions -------------------------------------------------------------


async def create_region(
    session: AsyncSession, *, code: str, name: str
) -> Region:
    """Register a region this deployment can place data in."""
    normalised = code.strip().lower()
    if not normalised:
        raise InvalidTenantError("A region code must not be empty")

    with system_scope():
        existing = (
            await session.execute(select(Region).where(Region.code == normalised))
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        region = Region(code=normalised, name=name)
        session.add(region)
        await session.commit()
        return region


async def get_region_by_code(session: AsyncSession, code: str) -> Region | None:
    """Look up a region by code."""
    with system_scope():
        return (
            await session.execute(
                select(Region).where(Region.code == code.strip().lower())
            )
        ).scalar_one_or_none()


async def list_regions(session: AsyncSession) -> Sequence[Region]:
    """Every registered region."""
    with system_scope():
        return (await session.execute(select(Region).order_by(Region.code))).scalars().all()


# --- Jurisdictions -------------------------------------------------------


async def create_jurisdiction(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    parent_id: uuid.UUID | None = None,
) -> Jurisdiction:
    """Register a legal or business geography.

    ``parent_id`` records containment but is **not traversed** by the PDP --
    see :class:`app.platform.models.Jurisdiction` and assumption A-33.
    """
    normalised = code.strip().upper()
    if not normalised:
        raise InvalidTenantError("A jurisdiction code must not be empty")

    with system_scope():
        existing = (
            await session.execute(
                select(Jurisdiction).where(Jurisdiction.code == normalised)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        jurisdiction = Jurisdiction(code=normalised, name=name, parent_id=parent_id)
        session.add(jurisdiction)
        await session.commit()
        return jurisdiction


async def get_jurisdiction_by_code(
    session: AsyncSession, code: str
) -> Jurisdiction | None:
    """Look up a jurisdiction by code."""
    with system_scope():
        return (
            await session.execute(
                select(Jurisdiction).where(Jurisdiction.code == code.strip().upper())
            )
        ).scalar_one_or_none()


# --- Tenants -------------------------------------------------------------


async def create_tenant(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    tenant_id: uuid.UUID | None = None,
    jurisdiction_id: uuid.UUID | None = None,
    residency_mode: str = ResidencyMode.UNRESTRICTED,
) -> Tenant:
    """Register a tenant.

    Platform-level: creating a tenant is by definition not done from inside
    one, so this runs in ``system_scope()``.
    """
    normalised = slug.strip().lower()
    if not normalised:
        raise InvalidTenantError("A tenant slug must not be empty")
    if residency_mode not in {ResidencyMode.UNRESTRICTED, ResidencyMode.STRICT}:
        raise InvalidTenantError(f"Unknown residency mode {residency_mode!r}")

    with system_scope():
        existing = (
            await session.execute(select(Tenant).where(Tenant.slug == normalised))
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateTenantError(f"Tenant {normalised!r} already exists")

        tenant = Tenant(
            slug=normalised,
            name=name,
            jurisdiction_id=jurisdiction_id,
            residency_mode=residency_mode,
        )
        if tenant_id is not None:
            tenant.id = tenant_id
        session.add(tenant)
        await session.commit()
        return tenant


async def assign_residency(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    relational: str | None = None,
    object_storage: str | None = None,
    processing: str | None = None,
    backup: str | None = None,
    residency_mode: str | None = None,
) -> Tenant:
    """Assign a tenant's data placement.

    Region arguments are codes. An unrecognised code raises rather than being
    ignored -- a mis-typed region that silently left a tenant unassigned would
    be a compliance failure reported as success.
    """
    with system_scope():
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            raise TenantNotFoundError(f"No tenant {tenant_id}")

        async def resolve(code: str | None) -> uuid.UUID | None:
            if code is None:
                return None
            region = (
                await session.execute(
                    select(Region).where(Region.code == code.strip().lower())
                )
            ).scalar_one_or_none()
            if region is None:
                raise RegionNotFoundError(f"No region {code!r}")
            return region.id

        if relational is not None:
            tenant.relational_region_id = await resolve(relational)
        if object_storage is not None:
            tenant.object_storage_region_id = await resolve(object_storage)
        if processing is not None:
            tenant.processing_region_id = await resolve(processing)
        if backup is not None:
            tenant.backup_region_id = await resolve(backup)
        if residency_mode is not None:
            if residency_mode not in {ResidencyMode.UNRESTRICTED, ResidencyMode.STRICT}:
                raise InvalidTenantError(f"Unknown residency mode {residency_mode!r}")
            tenant.residency_mode = residency_mode

        await session.commit()
        return tenant


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    """Look up a tenant by id."""
    # The registry is what defines tenants, so it cannot be filtered by one.
    with system_scope():
        return (
            await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Tenant | None:
    """Look up a tenant by slug."""
    with system_scope():
        return (
            await session.execute(
                select(Tenant).where(Tenant.slug == slug.strip().lower())
            )
        ).scalar_one_or_none()


async def list_tenants(session: AsyncSession) -> Sequence[Tenant]:
    """Every registered tenant. Platform-level by nature."""
    with system_scope():
        return (await session.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()


async def require_active_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    """Fetch a tenant, raising unless it exists and is active.

    The platform sentinel tenant is seeded **inactive**, so it can own
    unattributable audit events without ever being usable as a real tenant.
    """
    tenant = await get_tenant(session, tenant_id)
    if tenant is None or not tenant.is_active:
        raise TenantNotFoundError(f"No active tenant {tenant_id}")
    return tenant
