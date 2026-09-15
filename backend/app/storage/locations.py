"""Where a tenant's objects physically live.

**This module is the seam for A-19 (data residency).**

Today every tenant shares one bucket, which is correct while residency is
unspecified. But *where bytes live* is exactly the question residency asks, and
if the requirements turn out to mean physical residency rather than
jurisdiction, the answer has to be expressible somewhere. Putting bucket
selection behind a resolver now means that "somewhere" already exists.

Resolution is deliberately **per tenant**, not global, even though the default
implementation ignores the tenant. A global `settings.s3_bucket` read scattered
through the service layer would have to be hunted down and replaced; a resolver
is swapped once.

What a future residency policy would replace:

- :class:`SingleBucketResolver` -> a resolver mapping tenant to bucket/region
- nothing else in this package

What it would **not** fix, and must not be mistaken for a solution: the
relational data still lives in one shared PostgreSQL schema
(:doc:`ADR-0008 <../../../docs/adr/0008-shared-schema-multi-tenancy>`). If
residency is required, object storage is the easy half. See the A-19 entry in
``docs/architecture/assumptions.md`` for the full blast radius.

**No residency policy is implemented here, and none is assumed.**
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.storage.errors import StorageConfigurationError


@dataclass(frozen=True, slots=True)
class StorageLocation:
    """A bucket, a region, and an optional key prefix within it."""

    bucket: str
    region: str
    prefix: str = ""

    def __post_init__(self) -> None:
        if not self.bucket:
            raise StorageConfigurationError("Storage bucket is not configured")


@runtime_checkable
class LocationResolver(Protocol):
    """Decides which location a tenant's objects belong in."""

    def resolve(self, tenant_id: uuid.UUID) -> StorageLocation:
        """Return the location for this tenant's objects."""
        ...


class SingleBucketResolver:
    """Every tenant shares one bucket, separated by key prefix.

    The correct default while residency is unspecified, and the thing a
    residency policy would replace. Tenant separation is by key prefix and --
    more importantly -- by the tenant-scoped database rows that are the only
    way a key is ever obtained.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def resolve(self, tenant_id: uuid.UUID) -> StorageLocation:
        return StorageLocation(
            bucket=self._settings.s3_bucket,
            region=self._settings.s3_region,
        )


_resolver: LocationResolver | None = None


def get_location_resolver() -> LocationResolver:
    """The process-wide resolver."""
    global _resolver
    if _resolver is None:
        _resolver = SingleBucketResolver()
    return _resolver


def set_location_resolver(resolver: LocationResolver | None) -> None:
    """Replace the resolver. The extension point for a residency policy."""
    global _resolver
    _resolver = resolver
