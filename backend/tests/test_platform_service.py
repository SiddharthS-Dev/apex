"""Platform control plane: tenant registry, regions, jurisdictions, residency.

Against PostgreSQL 16. Covers the P02 relocation of ``Tenant`` out of the Policy
context, the foreign keys that relocation made legal, and the residency
abstraction.

The fixtures seed three tenants (`__platform__`, `tenant-a`, `tenant-b`) and one
region (`local`), because tenant-owned tables now carry a foreign key to
``tenant``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.tenancy import PLATFORM_TENANT_ID, system_scope, tenant_scope
from app.platform import service
from app.platform.models import Jurisdiction, Region, ResidencyMode, Tenant
from app.platform.residency import (
    DefaultRegionResolver,
    RegionUnavailableError,
    TenantResidency,
    TenantResidencyResolver,
)
from app.platform.service import (
    DuplicateTenantError,
    InvalidTenantError,
    RegionNotFoundError,
    TenantNotFoundError,
)

SEEDED = {"__platform__", "tenant-a", "tenant-b"}


# --- Tenant registry -----------------------------------------------------


async def test_tenant_can_be_registered(session: AsyncSession) -> None:
    tenant = await service.create_tenant(session, slug="acme", name="Acme Ltd")

    assert tenant.slug == "acme"
    assert tenant.is_active is True
    assert tenant.residency_mode == ResidencyMode.UNRESTRICTED


async def test_tenant_slug_is_normalised(session: AsyncSession) -> None:
    assert (await service.create_tenant(session, slug="  ACME  ", name="Acme")).slug == "acme"


async def test_duplicate_tenant_slug_is_refused(session: AsyncSession) -> None:
    await service.create_tenant(session, slug="acme", name="Acme")

    with pytest.raises(DuplicateTenantError):
        await service.create_tenant(session, slug="acme", name="Acme Again")


async def test_empty_tenant_slug_is_refused(session: AsyncSession) -> None:
    with pytest.raises(InvalidTenantError):
        await service.create_tenant(session, slug="   ", name="Nameless")


async def test_unknown_residency_mode_is_refused(session: AsyncSession) -> None:
    with pytest.raises(InvalidTenantError, match="residency mode"):
        await service.create_tenant(
            session, slug="odd", name="Odd", residency_mode="maybe"
        )


async def test_tenant_can_be_looked_up_by_id_and_slug(session: AsyncSession) -> None:
    created = await service.create_tenant(session, slug="acme", name="Acme")

    assert await service.get_tenant(session, created.id) is not None
    assert await service.get_tenant_by_slug(session, "ACME") is not None


async def test_listing_tenants_is_a_platform_operation(session: AsyncSession) -> None:
    await service.create_tenant(session, slug="globex", name="Globex")

    slugs = {t.slug for t in await service.list_tenants(session)}

    assert "globex" in slugs
    assert SEEDED <= slugs


async def test_inactive_tenant_is_not_usable(session: AsyncSession) -> None:
    tenant = await service.create_tenant(session, slug="acme", name="Acme")
    with system_scope():
        tenant.is_active = False
        await session.commit()

    with pytest.raises(TenantNotFoundError):
        await service.require_active_tenant(session, tenant.id)


async def test_unknown_tenant_is_refused(session: AsyncSession) -> None:
    with pytest.raises(TenantNotFoundError):
        await service.require_active_tenant(session, uuid.uuid4())


async def test_tenant_registry_is_global_not_tenant_scoped(
    session: AsyncSession,
) -> None:
    """The registry defines tenants, so it cannot be filtered by one."""
    await service.create_tenant(session, slug="acme", name="Acme")

    with tenant_scope(uuid.uuid4()):
        slugs = {t.slug for t in (await session.execute(select(Tenant))).scalars().all()}

    assert "acme" in slugs


# --- Platform sentinel ---------------------------------------------------


async def test_platform_sentinel_exists(session: AsyncSession) -> None:
    """Unattributable audit events need a tenant row to reference."""
    sentinel = await service.get_tenant(session, PLATFORM_TENANT_ID)

    assert sentinel is not None
    assert sentinel.slug == service.PLATFORM_TENANT_SLUG


async def test_platform_sentinel_is_inactive(session: AsyncSession) -> None:
    """It owns records; it must never be something anyone can log into."""
    sentinel = await service.get_tenant(session, PLATFORM_TENANT_ID)
    assert sentinel is not None
    assert sentinel.is_active is False

    with pytest.raises(TenantNotFoundError):
        await service.require_active_tenant(session, PLATFORM_TENANT_ID)


# --- Foreign key integrity -----------------------------------------------


async def test_unknown_tenant_id_is_rejected_by_the_database(
    session: AsyncSession,
) -> None:
    """A-18 resolved: the tenant edge is now enforced, not merely documented."""
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO app_user (id, tenant_id, email, display_name, "
                "is_active, created_at, updated_at) VALUES "
                "(gen_random_uuid(), gen_random_uuid(), 'x@example.com', '', "
                "true, now(), now())"
            )
        )
    await session.rollback()


async def test_deleting_a_tenant_with_data_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """ON DELETE RESTRICT -- audit immutability would be meaningless otherwise."""
    await session.execute(
        text(
            "INSERT INTO policy (id, tenant_id, name, description, effect, "
            "action, resource_type, is_active, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :t, 'p', '', 'allow', '*', '*', true, now(), now())"
        ),
        {"t": tenant_a},
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tenant_a})
    await session.rollback()


# --- Regions -------------------------------------------------------------


async def test_region_can_be_registered(session: AsyncSession) -> None:
    region = await service.create_region(session, code="EU-WEST-1", name="Ireland")

    assert region.code == "eu-west-1"


async def test_region_registration_is_idempotent(session: AsyncSession) -> None:
    first = await service.create_region(session, code="eu-west-1", name="Ireland")
    second = await service.create_region(session, code="eu-west-1", name="Ireland")

    assert first.id == second.id


async def test_seeded_default_region_exists(session: AsyncSession) -> None:
    assert await service.get_region_by_code(session, "local") is not None


# --- Jurisdictions -------------------------------------------------------


async def test_jurisdiction_can_be_registered(session: AsyncSession) -> None:
    jurisdiction = await service.create_jurisdiction(session, code="de", name="Germany")

    assert jurisdiction.code == "DE"


async def test_no_jurisdictions_are_seeded(session: AsyncSession) -> None:
    """A code list would be a fabrication under Master Prompt §35."""
    with system_scope():
        rows = (await session.execute(select(Jurisdiction))).scalars().all()

    assert rows == []


async def test_jurisdiction_parent_is_recorded_but_not_interpreted(
    session: AsyncSession,
) -> None:
    """A-33: containment is storable; nothing walks it."""
    eu = await service.create_jurisdiction(session, code="eu", name="European Union")
    de = await service.create_jurisdiction(
        session, code="de", name="Germany", parent_id=eu.id
    )

    assert de.parent_id == eu.id


# --- Residency -----------------------------------------------------------


async def test_default_resolver_places_everything_in_the_default_region(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    resolver = DefaultRegionResolver(Settings(default_region="local"))

    residency = await resolver.resolve(session, tenant_a)

    assert residency.require("relational") == "local"
    assert residency.require("object_storage") == "local"
    assert residency.require("processing") == "local"
    assert residency.require("backup") == "local"


async def test_unknown_tenant_resolves_to_unrestricted_defaults(
    session: AsyncSession,
) -> None:
    """An unknown tenant has declared no constraint, so there is none to keep."""
    resolver = TenantResidencyResolver(Settings(default_region="local"))

    residency = await resolver.resolve(session, uuid.uuid4())

    assert residency.is_strict is False
    assert residency.require("relational") == "local"


async def test_assigned_regions_are_resolved(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_region(session, code="eu-west-1", name="Ireland")
    await service.assign_residency(
        session, tenant_id=tenant_a, relational="eu-west-1", object_storage="eu-west-1"
    )

    residency = await TenantResidencyResolver(
        Settings(default_region="local")
    ).resolve(session, tenant_a)

    assert residency.require("relational") == "eu-west-1"
    assert residency.require("object_storage") == "eu-west-1"


async def test_unrestricted_tenant_falls_back_for_unassigned_placements(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_region(session, code="eu-west-1", name="Ireland")
    await service.assign_residency(session, tenant_id=tenant_a, relational="eu-west-1")

    residency = await TenantResidencyResolver(
        Settings(default_region="local")
    ).resolve(session, tenant_a)

    assert residency.require("relational") == "eu-west-1"
    assert residency.require("backup") == "local"


async def test_strict_residency_fails_closed_on_an_unassigned_placement(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """The decisive residency test. A fallback here is a compliance failure
    reported as success."""
    await service.create_region(session, code="eu-west-1", name="Ireland")
    await service.assign_residency(
        session,
        tenant_id=tenant_a,
        relational="eu-west-1",
        residency_mode=ResidencyMode.STRICT,
    )

    residency = await TenantResidencyResolver(
        Settings(default_region="local")
    ).resolve(session, tenant_a)

    assert residency.require("relational") == "eu-west-1"
    with pytest.raises(RegionUnavailableError, match="strict residency"):
        residency.require("backup")


async def test_strict_residency_with_every_placement_assigned_succeeds(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_region(session, code="eu-west-1", name="Ireland")
    await service.assign_residency(
        session,
        tenant_id=tenant_a,
        relational="eu-west-1",
        object_storage="eu-west-1",
        processing="eu-west-1",
        backup="eu-west-1",
        residency_mode=ResidencyMode.STRICT,
    )

    residency = await TenantResidencyResolver(
        Settings(default_region="local")
    ).resolve(session, tenant_a)

    assert residency.is_strict is True
    assert residency.require("backup") == "eu-west-1"


def test_strict_residency_refuses_without_touching_the_database() -> None:
    """`require` is pure, so the refusal is testable in isolation."""
    residency = TenantResidency(
        mode=ResidencyMode.STRICT, default_region="local", relational=None
    )

    with pytest.raises(RegionUnavailableError):
        residency.require("relational")


async def test_assigning_an_unknown_region_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """A mis-typed region that silently left a tenant unassigned would be a
    compliance failure reported as success."""
    with pytest.raises(RegionNotFoundError):
        await service.assign_residency(
            session, tenant_id=tenant_a, relational="atlantis-1"
        )


async def test_assigning_residency_to_an_unknown_tenant_is_refused(
    session: AsyncSession,
) -> None:
    with pytest.raises(TenantNotFoundError):
        await service.assign_residency(session, tenant_id=uuid.uuid4(), relational="local")


async def test_region_assignment_survives_a_reread(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_region(session, code="eu-west-1", name="Ireland")
    await service.assign_residency(session, tenant_id=tenant_a, backup="eu-west-1")

    tenant = await service.get_tenant(session, tenant_a)

    assert tenant is not None
    assert tenant.backup_region is not None
    assert tenant.backup_region.code == "eu-west-1"


async def test_regions_are_global_not_tenant_scoped(session: AsyncSession) -> None:
    await service.create_region(session, code="eu-west-1", name="Ireland")

    with tenant_scope(uuid.uuid4()):
        codes = {r.code for r in (await session.execute(select(Region))).scalars().all()}

    assert "eu-west-1" in codes
