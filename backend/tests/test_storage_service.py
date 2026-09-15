"""Tenant-aware storage service, against PostgreSQL 16.

The decisive property under test: a caller never supplies a key, so
cross-tenant and arbitrary-path access are not expressible. Every test here
addresses objects by ``StoredObject`` id, which is the only route the service
offers.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.tenancy import TenantContextMissingError, system_scope, tenant_scope
from app.storage import service as storage
from app.storage.backend import InMemoryObjectStore
from app.storage.errors import (
    InvalidObjectKeyError,
    ObjectNotFoundError,
    ObjectTooLargeError,
    UnsupportedContentTypeError,
)
from app.storage.locations import SingleBucketResolver
from app.storage.models import StoredObject

CONTENT = b"the quick brown fox"


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def settings() -> Settings:
    return Settings(s3_bucket="apex-test", storage_signed_url_ttl_seconds=300)


@pytest.fixture
def resolver(settings: Settings) -> SingleBucketResolver:
    return SingleBucketResolver(settings)


async def _upload(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_id: uuid.UUID,
    data: bytes = CONTENT,
    **kwargs: object,
) -> storage.UploadResult:
    return await storage.upload(
        session,
        tenant_id=tenant_id,
        data=data,
        store=store,
        resolver=resolver,
        settings=settings,
        **kwargs,  # type: ignore[arg-type]
    )


# --- Upload --------------------------------------------------------------


async def test_upload_stores_bytes_and_records_a_reference(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(
        session, store, resolver, settings, tenant_a, filename="a.txt",
        content_type="text/plain",
    )

    assert result.stored_object.size_bytes == len(CONTENT)
    assert result.stored_object.tenant_id == tenant_a
    assert await store.get(resolver.resolve(tenant_a), result.stored_object.object_key) == CONTENT


async def test_upload_records_the_content_hash(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_a)

    assert result.stored_object.content_hash == storage.compute_content_hash(CONTENT)
    assert len(result.stored_object.content_hash) == 64


async def test_filename_is_recorded_but_never_reaches_the_key(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(
        session, store, resolver, settings, tenant_a, filename="../../salary.pdf"
    )

    assert result.stored_object.original_filename == "../../salary.pdf"
    assert "salary" not in result.stored_object.object_key
    assert ".." not in result.stored_object.object_key


async def test_key_is_inside_the_tenants_prefix(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_a)

    assert f"tenants/{tenant_a}/" in result.stored_object.object_key


async def test_identical_content_is_deduplicated(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    first = await _upload(session, store, resolver, settings, tenant_a)
    second = await _upload(session, store, resolver, settings, tenant_a)

    assert second.deduplicated is True
    assert second.stored_object.id == first.stored_object.id


async def test_deduplication_does_not_cross_tenants(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Sharing a row across tenants would be a cross-tenant data leak."""
    first = await _upload(session, store, resolver, settings, tenant_a)
    second = await _upload(session, store, resolver, settings, tenant_b)

    assert second.deduplicated is False
    assert second.stored_object.id != first.stored_object.id


# --- Validation ----------------------------------------------------------


async def test_oversized_upload_is_refused(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    tenant_a: uuid.UUID,
) -> None:
    small = Settings(s3_bucket="apex-test", storage_max_object_bytes=8)

    with pytest.raises(ObjectTooLargeError):
        await _upload(session, store, resolver, small, tenant_a, data=b"x" * 100)


async def test_a_refused_upload_writes_nothing(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    tenant_a: uuid.UUID,
) -> None:
    """Validation happens before storage, so nothing is left behind."""
    small = Settings(s3_bucket="apex-test", storage_max_object_bytes=8)

    with pytest.raises(ObjectTooLargeError):
        await _upload(session, store, resolver, small, tenant_a, data=b"x" * 100)

    with tenant_scope(tenant_a):
        assert (await session.execute(select(StoredObject))).scalars().all() == []


async def test_disallowed_content_type_is_refused(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    tenant_a: uuid.UUID,
) -> None:
    restricted = Settings(
        s3_bucket="apex-test", storage_allowed_content_types="application/pdf"
    )

    with pytest.raises(UnsupportedContentTypeError):
        await _upload(
            session, store, resolver, restricted, tenant_a, content_type="text/html"
        )


async def test_allowed_content_type_passes(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    tenant_a: uuid.UUID,
) -> None:
    restricted = Settings(
        s3_bucket="apex-test", storage_allowed_content_types="application/pdf, text/plain"
    )

    result = await _upload(
        session, store, resolver, restricted, tenant_a, content_type="application/pdf"
    )

    assert result.stored_object.content_type == "application/pdf"


async def test_empty_allowlist_permits_any_type(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(
        session, store, resolver, settings, tenant_a, content_type="application/x-odd"
    )

    assert result.stored_object.content_type == "application/x-odd"


# --- Read ----------------------------------------------------------------


async def test_download_returns_the_bytes(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_a)

    data = await storage.download(
        session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
    )

    assert data == CONTENT


async def test_download_verifies_the_content_hash(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    """Corrupted or substituted bytes must not be served as if intact."""
    result = await _upload(session, store, resolver, settings, tenant_a)
    await store.put(
        resolver.resolve(tenant_a), result.stored_object.object_key, b"tampered"
    )

    with pytest.raises(ObjectNotFoundError, match="integrity check"):
        await storage.download(
            session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
        )


async def test_head_returns_metadata(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(
        session, store, resolver, settings, tenant_a, content_type="text/plain"
    )

    info = await storage.head(
        session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
    )

    assert info.size_bytes == len(CONTENT)
    assert info.content_type == "text/plain"


async def test_exists_reflects_presence(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_a)

    assert await storage.exists(
        session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
    )
    assert not await storage.exists(
        session, tenant_id=tenant_a, object_id=uuid.uuid4(), store=store
    )


async def test_missing_object_raises_not_found(
    session: AsyncSession,
    store: InMemoryObjectStore,
    tenant_a: uuid.UUID,
) -> None:
    with pytest.raises(ObjectNotFoundError):
        await storage.download(
            session, tenant_id=tenant_a, object_id=uuid.uuid4(), store=store
        )


# --- Signed URLs ---------------------------------------------------------


async def test_signed_url_is_generated(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_a)

    url = await storage.signed_download_url(
        session,
        tenant_id=tenant_a,
        object_id=result.stored_object.id,
        store=store,
        settings=settings,
    )

    assert result.stored_object.object_key in url


async def test_signed_url_ttl_cannot_exceed_the_configured_ceiling(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    tenant_a: uuid.UUID,
) -> None:
    """A URL outliving its ceiling is a credential nobody can revoke."""
    capped = Settings(s3_bucket="apex-test", storage_signed_url_ttl_seconds=60)
    result = await _upload(session, store, resolver, capped, tenant_a)

    url = await storage.signed_download_url(
        session,
        tenant_id=tenant_a,
        object_id=result.stored_object.id,
        store=store,
        settings=capped,
        expires_in=86_400,
    )

    expires_at = int(url.rsplit("expires=", 1)[1])
    import time

    assert expires_at - int(time.time()) <= 61


async def test_signed_url_for_another_tenants_object_is_refused(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_b)

    with pytest.raises(ObjectNotFoundError):
        await storage.signed_download_url(
            session,
            tenant_id=tenant_a,
            object_id=result.stored_object.id,
            store=store,
            settings=settings,
        )


# --- Delete --------------------------------------------------------------


async def test_delete_removes_bytes_but_keeps_the_reference(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    """Provenance must still resolve what a past reference pointed at."""
    result = await _upload(session, store, resolver, settings, tenant_a)

    assert await storage.delete(
        session,
        tenant_id=tenant_a,
        object_id=result.stored_object.id,
        reason="test",
        store=store,
    )

    assert not await store.exists(
        resolver.resolve(tenant_a), result.stored_object.object_key
    )
    with tenant_scope(tenant_a):
        row = (await session.execute(select(StoredObject))).scalars().one()
    assert row.is_deleted is True
    assert row.deletion_reason == "test"


async def test_deleted_object_cannot_be_downloaded(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_a)
    await storage.delete(
        session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
    )

    with pytest.raises(ObjectNotFoundError):
        await storage.download(
            session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
        )


async def test_deleting_twice_is_reported_honestly(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_a)
    await storage.delete(
        session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
    )

    assert not await storage.delete(
        session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
    )


# --- Tenant isolation ----------------------------------------------------


async def test_references_are_invisible_across_tenants(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _upload(session, store, resolver, settings, tenant_b)

    assert list(await storage.list_references(session, tenant_id=tenant_a)) == []


async def test_knowing_the_id_is_not_enough(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """The decisive cross-tenant test."""
    result = await _upload(session, store, resolver, settings, tenant_b)

    assert (
        await storage.get_reference(
            session, tenant_id=tenant_a, object_id=result.stored_object.id
        )
        is None
    )
    with pytest.raises(ObjectNotFoundError):
        await storage.download(
            session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
        )


async def test_cross_tenant_delete_is_refused(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    result = await _upload(session, store, resolver, settings, tenant_b)

    assert not await storage.delete(
        session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
    )
    assert await store.exists(
        resolver.resolve(tenant_b), result.stored_object.object_key
    )


async def test_reading_references_without_a_tenant_is_refused(
    session: AsyncSession,
) -> None:
    with pytest.raises(TenantContextMissingError):
        await session.execute(select(StoredObject))


async def test_system_scope_sees_every_tenants_references(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _upload(session, store, resolver, settings, tenant_a, data=b"a")
    await _upload(session, store, resolver, settings, tenant_b, data=b"b")

    with system_scope():
        rows = (await session.execute(select(StoredObject))).scalars().all()

    assert len(rows) == 2


async def test_a_tampered_key_in_the_database_is_refused(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    """Defence in depth: keys read back are re-validated before use."""
    result = await _upload(session, store, resolver, settings, tenant_a)

    with tenant_scope(tenant_a):
        row = (await session.execute(select(StoredObject))).scalars().one()
        row.object_key = "../../../etc/passwd"
        await session.commit()

    with pytest.raises(InvalidObjectKeyError):
        await storage.download(
            session, tenant_id=tenant_a, object_id=result.stored_object.id, store=store
        )


async def test_bucket_is_taken_from_the_row_not_from_configuration(
    session: AsyncSession,
    store: InMemoryObjectStore,
    resolver: SingleBucketResolver,
    settings: Settings,
    tenant_a: uuid.UUID,
) -> None:
    """Objects written before a bucket change must stay readable (A-19)."""
    result = await _upload(session, store, resolver, settings, tenant_a)

    assert result.stored_object.bucket == settings.s3_bucket
