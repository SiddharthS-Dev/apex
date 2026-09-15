"""Where a tenant's objects physically live.

**This module is the seam for A-19 (data residency).**

Master Prompt §10 resolved A-19: geography means **both** jurisdiction and
physical residency, modelled separately. This module handles the storage half of
residency -- which bucket, in which region, holds a tenant's bytes.

:class:`RegionAwareBucketResolver` maps a **region code** to a bucket via
configuration (``APEX_S3_BUCKETS`` as ``region=bucket``). The caller supplies the
region, having resolved it from the tenant's residency, so this stays a pure
mapping with no database access.

With a single configured bucket the behaviour is identical to the arrangement it
replaces -- which is the point: P02 delivers residency *architecture*, not a
multi-region deployment.

What this does **not** solve, and must not be mistaken for a solution:
relational data placement. Object storage is the easy half. See ADR-0010 and the
A-19 entry in ``docs/architecture/assumptions.md``.
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
    #: The S3 *signing* region, which is a protocol detail.
    region: str
    prefix: str = ""
    #: The APEX residency region this placement satisfies. Recorded on each
    #: stored object so where bytes live is a fact, not an inference from
    #: whatever configuration happens to be current.
    placement_region: str | None = None

    def __post_init__(self) -> None:
        if not self.bucket:
            raise StorageConfigurationError("Storage bucket is not configured")


@runtime_checkable
class LocationResolver(Protocol):
    """Decides which location a tenant's objects belong in."""

    def resolve(
        self, tenant_id: uuid.UUID, region: str | None = None
    ) -> StorageLocation:
        """Return the location for this tenant's objects in a region."""
        ...


def parse_bucket_map(raw: str) -> dict[str, str]:
    """Parse ``region=bucket,region=bucket`` into a mapping.

    A malformed entry raises rather than being skipped: a silently dropped
    region would place a tenant's bytes somewhere other than intended.
    """
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        region, separator, bucket = item.partition("=")
        if not separator or not region.strip() or not bucket.strip():
            raise ValueError(
                f"Malformed bucket mapping entry {item!r}; expected 'region=bucket'"
            )
        mapping[region.strip()] = bucket.strip()
    return mapping


class RegionAwareBucketResolver:
    """Maps a region code to the bucket holding that region's objects.

    A pure mapping: the caller resolves the tenant's residency and passes the
    region in, so nothing here touches the database. With one configured bucket
    this behaves exactly as the previous single-bucket resolver did.

    Tenant separation remains by key prefix and -- far more importantly -- by
    the tenant-scoped database rows that are the only way a key is ever
    obtained. Bucket separation is about *placement*, not isolation.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def buckets(self) -> dict[str, str]:
        """Region to bucket. Falls back to the single-bucket configuration."""
        configured = parse_bucket_map(self._settings.s3_buckets)
        if configured:
            return configured
        return {self._settings.default_region: self._settings.s3_bucket}

    def resolve(
        self, tenant_id: uuid.UUID, region: str | None = None
    ) -> StorageLocation:
        target = region or self._settings.default_region
        buckets = self.buckets()

        bucket = buckets.get(target)
        if bucket is None:
            raise StorageConfigurationError(
                f"No bucket configured for region {target!r}; "
                f"configured regions are {sorted(buckets)}"
            )

        return StorageLocation(
            bucket=bucket,
            region=self._settings.s3_region,
            placement_region=target,
        )


#: Retained name for the previous resolver. Identical behaviour with one bucket.
SingleBucketResolver = RegionAwareBucketResolver


_resolver: LocationResolver | None = None


def get_location_resolver() -> LocationResolver:
    """The process-wide resolver."""
    global _resolver
    if _resolver is None:
        _resolver = RegionAwareBucketResolver()
    return _resolver


def set_location_resolver(resolver: LocationResolver | None) -> None:
    """Replace the resolver. The extension point for a residency policy."""
    global _resolver
    _resolver = resolver
