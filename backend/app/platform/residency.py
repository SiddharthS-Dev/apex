"""Residency resolution — which regions a tenant's data may occupy.

**This is residency *architecture*, not residency *enforcement*.** The
abstraction records intent and routes accordingly; actually holding a tenant's
data in Frankfurt requires a deployment in Frankfurt. P02 delivers the former.
Claiming the latter because the abstraction exists is precisely what Master
Prompt §10 forbids.

What the abstraction does do today, and it is not nothing:

- a tenant's four regions are recorded and resolvable
- an **unrestricted** tenant falls back to the default region, which is the
  current single-region behaviour, unchanged
- a **strict** tenant with an unassigned region is **refused**. There is no
  silent fallback. A strict tenant on a single-region deployment is either
  genuinely assigned to that region -- and therefore genuinely compliant -- or
  its operations fail loudly

That last rule is the whole point. A residency model that quietly does the
wrong thing when it cannot do the right thing is worse than no model, because
it reports compliance it has not achieved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import system_scope
from app.platform.models import ResidencyMode, Tenant

#: The four placements a tenant's data occupies.
RegionKind = Literal["relational", "object_storage", "processing", "backup"]


class ResidencyError(Exception):
    """A residency constraint could not be satisfied."""


class RegionUnavailableError(ResidencyError):
    """A strict tenant requires a region that is unassigned or unavailable.

    Raised rather than falling back. See the module docstring.
    """


@dataclass(frozen=True, slots=True)
class TenantResidency:
    """Where one tenant's data may live."""

    mode: str
    default_region: str
    relational: str | None = None
    object_storage: str | None = None
    processing: str | None = None
    backup: str | None = None

    @property
    def is_strict(self) -> bool:
        return self.mode == ResidencyMode.STRICT

    def require(self, kind: RegionKind) -> str:
        """Return the region for one placement, or refuse.

        An unrestricted tenant falls back to the default region. A strict
        tenant with that placement unassigned raises -- the fallback is exactly
        what strictness forbids.
        """
        assigned: str | None = getattr(self, kind)
        if assigned is not None:
            return assigned
        if self.is_strict:
            raise RegionUnavailableError(
                f"Tenant has strict residency but no {kind} region is assigned; "
                "refusing rather than falling back to the default region"
            )
        return self.default_region


@runtime_checkable
class ResidencyResolver(Protocol):
    """Resolves a tenant's residency."""

    async def resolve(
        self, session: AsyncSession, tenant_id: uuid.UUID
    ) -> TenantResidency:
        """Return where this tenant's data may live."""
        ...


class DefaultRegionResolver:
    """Every tenant occupies the configured default region.

    The single-region development behaviour, and the resolver used where no
    registry lookup is possible or wanted. Never returns a strict residency,
    because it does not consult the tenant that would declare one.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def resolve(
        self, session: AsyncSession, tenant_id: uuid.UUID
    ) -> TenantResidency:
        region = self._settings.default_region
        return TenantResidency(
            mode=ResidencyMode.UNRESTRICTED,
            default_region=region,
            relational=region,
            object_storage=region,
            processing=region,
            backup=region,
        )


class TenantResidencyResolver:
    """Reads residency from the tenant registry.

    A tenant that is not in the registry resolves to unrestricted defaults.
    That is deliberate and correct: an unknown tenant has declared no
    constraint, so there is none to enforce. Only a tenant that *has* declared
    strict residency can be failed closed -- and it will be.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def resolve(
        self, session: AsyncSession, tenant_id: uuid.UUID
    ) -> TenantResidency:
        default = self._settings.default_region

        # The registry defines tenants, so it cannot be filtered by one.
        with system_scope():
            tenant = (
                await session.execute(select(Tenant).where(Tenant.id == tenant_id))
            ).scalar_one_or_none()

        if tenant is None:
            return TenantResidency(
                mode=ResidencyMode.UNRESTRICTED,
                default_region=default,
                relational=default,
                object_storage=default,
                processing=default,
                backup=default,
            )

        def code(region: object) -> str | None:
            return getattr(region, "code", None) if region is not None else None

        return TenantResidency(
            mode=tenant.residency_mode,
            default_region=default,
            relational=code(tenant.relational_region),
            object_storage=code(tenant.object_storage_region),
            processing=code(tenant.processing_region),
            backup=code(tenant.backup_region),
        )


_resolver: ResidencyResolver | None = None


def get_residency_resolver() -> ResidencyResolver:
    """The process-wide residency resolver."""
    global _resolver
    if _resolver is None:
        _resolver = TenantResidencyResolver()
    return _resolver


def set_residency_resolver(resolver: ResidencyResolver | None) -> None:
    """Replace the residency resolver. Used by tests and by deployment wiring."""
    global _resolver
    _resolver = resolver
