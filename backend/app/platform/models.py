"""Tenant registry, regions and jurisdictions.

All three are **global**, not tenant-scoped. A registry that defines tenants
cannot be filtered by the tenant it is looking for.

``Tenant`` moved here from ``app.policy`` in P02. The table name is unchanged,
so the relocation is a code move rather than a data migration -- but its
position in the context map changed, which is what allows the tenant foreign
keys added in the same commit.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import GlobalBase


class ResidencyMode(StrEnum):
    """How strictly a tenant's data placement is constrained."""

    #: No residency constraint. Unassigned regions fall back to the default.
    UNRESTRICTED = "unrestricted"

    #: Data must remain in the assigned regions. An unassigned or unavailable
    #: region is a **refusal**, never a fallback -- silently placing a strict
    #: tenant's data in the default region would be a compliance failure
    #: reported as success.
    STRICT = "strict"


class Region(GlobalBase):
    """A physical location data may live in.

    A deployment concern, distinct from :class:`Jurisdiction`. Held as data
    rather than configuration so a tenant can be assigned to a region without
    a deploy.
    """

    __tablename__ = "region"
    __table_args__ = (UniqueConstraint("code", name="uq_region_code"),)

    #: Deployment-defined, e.g. ``eu-west-1`` or ``local``.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Region {self.code}>"


class Jurisdiction(GlobalBase):
    """A legal or business geography governing a tenant.

    **Hierarchy is declared but not evaluated.** ``parent_id`` exists so a
    containment relationship can be expressed later, but nothing walks it:
    whether an EU-jurisdiction rule applies to a DE-jurisdiction subject is not
    specified in Master Prompt §10, and inventing that answer would put an
    unapproved legal interpretation into the authorization path. Recorded as
    assumption **A-33**.

    No jurisdictions are seeded, for the same reason -- a code list would be a
    fabrication under §35.
    """

    __tablename__ = "jurisdiction"
    __table_args__ = (UniqueConstraint("code", name="uq_jurisdiction_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: Declared for extensibility. Not traversed -- see the class docstring.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jurisdiction.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Jurisdiction {self.code}>"


class Tenant(GlobalBase):
    """A tenant of the platform.

    Global rather than tenant-scoped: the registry is what *defines* tenants,
    so it cannot itself be filtered by one. Reading it across tenants is a
    platform operation and requires ``system_scope()``.

    The four region columns are the residency abstraction required by Master
    Prompt §10. They are nullable: ``NULL`` means "unassigned", which an
    unrestricted tenant resolves to the default region and a **strict** tenant
    treats as a refusal.
    """

    __tablename__ = "tenant"
    __table_args__ = (UniqueConstraint("slug", name="uq_tenant_slug"),)

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Jurisdiction: which rules apply ---------------------------------
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jurisdiction.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # --- Residency: where bytes physically sit ---------------------------
    residency_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ResidencyMode.UNRESTRICTED,
        server_default=ResidencyMode.UNRESTRICTED.value,
    )
    relational_region_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("region.id", ondelete="RESTRICT"), nullable=True
    )
    object_storage_region_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("region.id", ondelete="RESTRICT"), nullable=True
    )
    processing_region_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("region.id", ondelete="RESTRICT"), nullable=True
    )
    backup_region_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("region.id", ondelete="RESTRICT"), nullable=True
    )

    jurisdiction: Mapped[Jurisdiction | None] = relationship(lazy="joined")
    relational_region: Mapped[Region | None] = relationship(
        lazy="joined", foreign_keys=[relational_region_id]
    )
    object_storage_region: Mapped[Region | None] = relationship(
        lazy="joined", foreign_keys=[object_storage_region_id]
    )
    processing_region: Mapped[Region | None] = relationship(
        lazy="joined", foreign_keys=[processing_region_id]
    )
    backup_region: Mapped[Region | None] = relationship(
        lazy="joined", foreign_keys=[backup_region_id]
    )

    def __repr__(self) -> str:
        return f"<Tenant {self.slug} residency={self.residency_mode}>"
