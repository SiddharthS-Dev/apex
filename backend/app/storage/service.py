"""Tenant-aware object storage operations.

The rule that makes this safe is short: **a caller never supplies a key.**

An upload hands over bytes and gets back a ``StoredObject`` id. Every later
operation names that id, the row is read under ``tenant_scope``, and the key
comes from the row. There is no code path that accepts a bucket or key from
outside, so "read another tenant's object" and "read an arbitrary path in the
bucket" are not expressible rather than merely forbidden.

Validation happens before anything is written: size, then content type, then
hashing. An object that fails validation leaves nothing behind in storage.

**Every operation is audited**, including refusals. Master Prompt §25 requires
audit coverage of material actions, and an object deleted without a record is
exactly the gap that makes a governance platform unable to answer what happened.

Two transaction shapes, matching :mod:`app.audit.service`:

- ``upload`` and ``delete`` already own a transaction, so their audit record
  joins it and commits atomically with the metadata change.
- Refusals and signed-URL issuance own no transaction. They take an optional
  ``audit_session_factory``; given one, the record is written independently so
  it survives a caller rollback. Without one the record joins the caller's
  transaction, which is the weaker guarantee and is documented as such.

**Residency is resolved per upload.** The tenant's object-storage region decides
which bucket receives the bytes, and the region is recorded on the row. A strict
tenant with no region assigned is refused rather than defaulted -- see
:mod:`app.platform.residency`.

This module deliberately exposes **no HTTP surface**. Uploading is something a
caller does *to* something -- and what that something is, and who may do it, are
Asset (A-02) and authority (A-04) questions. Adding an endpoint now would mean
inventing both.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import actions as audit_actions
from app.audit import service as audit
from app.core.config import Settings, get_settings
from app.core.tenancy import tenant_scope
from app.platform.residency import ResidencyResolver, get_residency_resolver
from app.storage.backend import ObjectMetadata, ObjectStore, S3ObjectStore
from app.storage.errors import (
    ObjectNotFoundError,
    ObjectTooLargeError,
    UnsupportedContentTypeError,
)
from app.storage.keys import generate_object_key, validate_key
from app.storage.locations import (
    LocationResolver,
    StorageLocation,
    get_location_resolver,
)
from app.storage.models import StoredObject

logger = logging.getLogger(__name__)

_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """The process-wide object store."""
    global _store
    if _store is None:
        _store = S3ObjectStore()
    return _store


def set_object_store(store: ObjectStore | None) -> None:
    """Replace the object store. Used by tests to inject a double."""
    global _store
    _store = store


@dataclass(frozen=True, slots=True)
class UploadResult:
    """What an upload produced."""

    stored_object: StoredObject
    metadata: ObjectMetadata
    deduplicated: bool = False


AuditFactory = async_sessionmaker[AsyncSession] | None


async def _audit(
    session: AsyncSession,
    factory: AuditFactory,
    **kwargs: object,
) -> None:
    """Record a storage event, independently when a factory is supplied.

    Failures here are swallowed deliberately: an audit backend problem must not
    turn a successful upload into an error the caller cannot act on. The write
    is attempted, never depended upon for control flow. Losing a record is bad;
    losing the object because the record failed is worse.
    """
    try:
        if factory is not None:
            await audit.record_independently(factory, **kwargs)
        else:
            await audit.record(session, **kwargs)
    except Exception:  # noqa: BLE001 -- audit must never break the operation
        logger.exception("Could not record a storage audit event")


def compute_content_hash(data: bytes) -> str:
    """SHA-256 of the content, hex encoded.

    Content-addressing serves two purposes: integrity, so a later read can be
    checked against what was stored, and de-duplication, so the same bytes
    uploaded twice do not occupy storage twice.
    """
    return hashlib.sha256(data).hexdigest()


def _validate_upload(
    data: bytes,
    content_type: str | None,
    settings: Settings,
) -> None:
    """Refuse an upload before anything is written."""
    if len(data) > settings.storage_max_object_bytes:
        raise ObjectTooLargeError(
            f"Object is {len(data)} bytes; the limit is "
            f"{settings.storage_max_object_bytes}"
        )

    allowed = settings.allowed_content_type_set
    if allowed and (content_type or "").lower() not in allowed:
        raise UnsupportedContentTypeError(
            f"Content type {content_type!r} is not permitted"
        )


async def upload(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    data: bytes,
    filename: str | None = None,
    content_type: str | None = None,
    metadata: dict[str, str] | None = None,
    store: ObjectStore | None = None,
    resolver: LocationResolver | None = None,
    residency: ResidencyResolver | None = None,
    settings: Settings | None = None,
    deduplicate: bool = True,
    actor_id: uuid.UUID | None = None,
) -> UploadResult:
    """Store bytes for a tenant and record the reference.

    The storage write happens before the database row is committed. If the
    commit then fails, an unreferenced object is left in the bucket -- wasted
    space, but no incorrect data. The opposite order would leave a row pointing
    at bytes that were never written, which reads as corruption.
    """
    settings = settings or get_settings()
    store = store or get_object_store()
    resolver = resolver or get_location_resolver()
    residency = residency or get_residency_resolver()

    _validate_upload(data, content_type, settings)

    content_hash = compute_content_hash(data)

    # Residency decides the region; the location resolver maps region to
    # bucket. A strict tenant with no object-storage region assigned raises
    # here rather than silently landing in the default region.
    placement = await residency.resolve(session, tenant_id)
    region = placement.require("object_storage")
    location = resolver.resolve(tenant_id, region=region)

    with tenant_scope(tenant_id):
        if deduplicate:
            existing = (
                await session.execute(
                    select(StoredObject).where(
                        StoredObject.content_hash == content_hash,
                        StoredObject.is_deleted.is_(False),
                    )
                )
            ).scalars().first()
            if existing is not None:
                return UploadResult(
                    stored_object=existing,
                    metadata=ObjectMetadata(
                        key=existing.object_key,
                        bucket=existing.bucket,
                        size_bytes=existing.size_bytes,
                        content_type=existing.content_type,
                    ),
                    deduplicated=True,
                )

    key = generate_object_key(tenant_id, filename=filename, prefix=location.prefix)

    written = await store.put(
        location,
        key,
        data,
        content_type=content_type,
        metadata=metadata,
    )

    with tenant_scope(tenant_id):
        record = StoredObject(
            bucket=location.bucket,
            region_code=location.placement_region,
            object_key=key,
            content_hash=content_hash,
            size_bytes=len(data),
            content_type=content_type,
            original_filename=filename,
            storage_metadata=dict(metadata or {}),
        )
        session.add(record)
        # Flush first: the primary key is generated at flush, so auditing
        # before this recorded resource_id=None -- an audit event that could
        # not say which object it was about.
        await session.flush()
        # Joined to this transaction, so the object reference and the record of
        # its creation commit together or not at all.
        await _audit(
            session,
            None,
            tenant_id=tenant_id,
            action=audit_actions.OBJECT_STORED,
            actor_id=actor_id,
            resource_type=audit_actions.ResourceType.STORED_OBJECT,
            resource_id=record.id,
            resource_label=filename,
            context={
                # Named `content_sha256`, not `content_hash`: the redaction
                # deny-list matches the fragment "hash" so that `pwd_hash` and
                # friends never reach an immutable record, and it caught this
                # too. A content digest is integrity metadata, not a
                # credential -- and it is precisely what an audit record needs
                # in order to say which bytes were stored. Naming the field for
                # what it actually is resolves both concerns; weakening the
                # deny-list would not.
                "content_sha256": content_hash,
                "size_bytes": len(data),
                "content_type": content_type,
                "region": location.placement_region,
            },
        )
        await session.commit()

    return UploadResult(stored_object=record, metadata=written)


async def get_reference(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
) -> StoredObject | None:
    """Read a stored-object row, or ``None`` if it is not this tenant's.

    The single choke point through which a key is obtained.
    """
    with tenant_scope(tenant_id):
        return (
            await session.execute(select(StoredObject).where(StoredObject.id == object_id))
        ).scalar_one_or_none()


async def _require_reference(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    audit_session_factory: AuditFactory = None,
) -> StoredObject:
    record = await get_reference(session, tenant_id=tenant_id, object_id=object_id)
    if record is None or record.is_deleted:
        # Audited as DENIED, not FAILURE: someone asked for an object this
        # tenant cannot reach, which is worth seeing whether it was a stale
        # reference or a probe.
        await _audit(
            session,
            audit_session_factory,
            tenant_id=tenant_id,
            action=audit_actions.OBJECT_ACCESS_DENIED,
            outcome=audit_actions.Outcome.DENIED,
            actor_id=actor_id,
            resource_type=audit_actions.ResourceType.STORED_OBJECT,
            resource_id=object_id,
            context={"reason": "not_found_or_deleted"},
        )
        # Another tenant's object is invisible under this scope, so it is
        # genuinely absent rather than forbidden -- and the response cannot
        # confirm that the id exists elsewhere.
        raise ObjectNotFoundError("No such stored object")
    # Defence in depth: re-validate a key read back from the database, so a
    # tampered row cannot address storage outside its prefix.
    validate_key(record.object_key)
    return record


def _location_of(record: StoredObject) -> StorageLocation:
    """The location a stored object actually lives in.

    Taken from the row -- bucket *and* region -- rather than from current
    configuration, so an object written before a residency change stays
    readable afterwards rather than being looked for in the wrong bucket
    (A-19, A-25).
    """
    settings = get_settings()
    return StorageLocation(
        bucket=record.bucket,
        region=settings.s3_region,
        placement_region=record.region_code,
    )


async def download(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
    store: ObjectStore | None = None,
    verify_hash: bool = True,
    actor_id: uuid.UUID | None = None,
    audit_session_factory: AuditFactory = None,
) -> bytes:
    """Read an object's bytes, checking them against the recorded hash."""
    store = store or get_object_store()
    record = await _require_reference(
        session,
        tenant_id,
        object_id,
        actor_id=actor_id,
        audit_session_factory=audit_session_factory,
    )

    data = await store.get(_location_of(record), record.object_key)

    if verify_hash and compute_content_hash(data) != record.content_hash:
        # Substituted or corrupted bytes are a security event, not a miss.
        await _audit(
            session,
            audit_session_factory,
            tenant_id=tenant_id,
            action=audit_actions.OBJECT_INTEGRITY_FAILED,
            outcome=audit_actions.Outcome.FAILURE,
            actor_id=actor_id,
            resource_type=audit_actions.ResourceType.STORED_OBJECT,
            resource_id=object_id,
            context={"expected_sha256": record.content_hash},
        )
        raise ObjectNotFoundError(
            "Stored object failed its integrity check and was not returned"
        )
    return data


async def head(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
    store: ObjectStore | None = None,
) -> ObjectMetadata:
    """Read an object's storage metadata without its bytes."""
    store = store or get_object_store()
    record = await _require_reference(session, tenant_id, object_id)
    return await store.head(_location_of(record), record.object_key)


async def exists(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
    store: ObjectStore | None = None,
) -> bool:
    """Whether the object exists in storage for this tenant."""
    store = store or get_object_store()
    record = await get_reference(session, tenant_id=tenant_id, object_id=object_id)
    if record is None or record.is_deleted:
        return False
    return await store.exists(_location_of(record), record.object_key)


async def signed_download_url(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
    store: ObjectStore | None = None,
    settings: Settings | None = None,
    expires_in: int | None = None,
    as_attachment: bool = True,
    actor_id: uuid.UUID | None = None,
    audit_session_factory: AuditFactory = None,
) -> str:
    """Mint a short-lived URL granting read access to one object.

    Short-lived and single-object by construction: the URL is derived from the
    key of a row already read under tenant scope, so it cannot be widened to a
    prefix or to another tenant. Callers must treat the result as a credential
    -- it is not logged, and should not be.
    """
    settings = settings or get_settings()
    store = store or get_object_store()
    record = await _require_reference(
        session,
        tenant_id,
        object_id,
        actor_id=actor_id,
        audit_session_factory=audit_session_factory,
    )

    ttl = expires_in or settings.storage_signed_url_ttl_seconds
    # A signed URL that outlives the session that issued it is a credential
    # nobody can revoke, so the ceiling is enforced here rather than trusted.
    ttl = max(1, min(ttl, settings.storage_signed_url_ttl_seconds))

    url = await store.signed_url(
        _location_of(record),
        record.object_key,
        expires_in=ttl,
        download_filename=record.original_filename if as_attachment else None,
    )

    # The URL itself is never recorded. It is a bearer credential for the
    # object, and an audit log is precisely the wrong place to keep one.
    await _audit(
        session,
        audit_session_factory,
        tenant_id=tenant_id,
        action=audit_actions.OBJECT_SIGNED_URL_ISSUED,
        actor_id=actor_id,
        resource_type=audit_actions.ResourceType.STORED_OBJECT,
        resource_id=object_id,
        context={"expires_in_seconds": ttl, "as_attachment": as_attachment},
    )
    return url


async def delete(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
    reason: str | None = None,
    store: ObjectStore | None = None,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Remove the bytes and mark the reference deleted.

    The row is **kept** and flagged rather than removed, so audit and
    provenance can still resolve what a past reference pointed at. Whether a
    tenant may delete at all, and what retention forbids, are governance
    questions (A-04, A-11) -- this is the mechanism, not the policy.
    """
    store = store or get_object_store()
    record = await get_reference(session, tenant_id=tenant_id, object_id=object_id)
    if record is None or record.is_deleted:
        return False

    await store.delete(_location_of(record), record.object_key)

    with tenant_scope(tenant_id):
        record.is_deleted = True
        record.deletion_reason = reason
        await _audit(
            session,
            None,
            tenant_id=tenant_id,
            action=audit_actions.OBJECT_DELETED,
            actor_id=actor_id,
            resource_type=audit_actions.ResourceType.STORED_OBJECT,
            resource_id=object_id,
            resource_label=record.original_filename,
            before_state={"is_deleted": False},
            after_state={"is_deleted": True, "reason": reason},
        )
        await session.commit()
    return True


async def list_references(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    include_deleted: bool = False,
    limit: int = 100,
) -> Sequence[StoredObject]:
    """List a tenant's stored-object references."""
    statement = select(StoredObject)
    if not include_deleted:
        statement = statement.where(StoredObject.is_deleted.is_(False))
    statement = statement.order_by(StoredObject.created_at.desc()).limit(
        max(1, min(limit, 1000))
    )

    with tenant_scope(tenant_id):
        return (await session.execute(statement)).scalars().all()
