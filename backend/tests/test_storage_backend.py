"""Object store implementations.

The same contract is exercised twice: once against the in-memory double, and
once against a **real MinIO server**. Running both is the point -- an
abstraction only tested against its own test double proves nothing about the
thing it abstracts.

The MinIO tests skip when no server is reachable, so the suite stays runnable
without Docker.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.storage.backend import (
    InMemoryObjectStore,
    ObjectStore,
    S3ObjectStore,
    stream_to_bytes,
)
from app.storage.errors import (
    ObjectNotFoundError,
    ObjectTooLargeError,
    StorageConfigurationError,
    StorageUnavailableError,
)
from app.storage.keys import generate_object_key
from app.storage.locations import SingleBucketResolver, StorageLocation

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")

MINIO_ENDPOINT = os.environ.get("APEX_TEST_S3_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("APEX_TEST_S3_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("APEX_TEST_S3_SECRET_KEY", "")


def _minio_settings(bucket: str) -> Settings:
    return Settings(
        s3_endpoint=MINIO_ENDPOINT,
        s3_bucket=bucket,
        s3_access_key=MINIO_ACCESS_KEY,
        s3_secret_key=MINIO_SECRET_KEY,
        s3_force_path_style=True,
        s3_connect_timeout=3,
        s3_read_timeout=10,
    )


@pytest.fixture
def memory_store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def location() -> StorageLocation:
    return StorageLocation(bucket="apex-test", region="us-east-1")


@pytest_asyncio.fixture
async def minio_store() -> AsyncIterator[tuple[S3ObjectStore, StorageLocation]]:
    """A real MinIO-backed store, or skip."""
    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        pytest.skip("APEX_TEST_S3_ACCESS_KEY / _SECRET_KEY not set")

    bucket = f"apex-test-{uuid.uuid4().hex[:12]}"
    store = S3ObjectStore(_minio_settings(bucket))
    location = StorageLocation(bucket=bucket, region="us-east-1")

    try:
        await store.ensure_bucket(location)
    except (StorageUnavailableError, StorageConfigurationError) as exc:
        pytest.skip(f"No MinIO at {MINIO_ENDPOINT}: {type(exc).__name__}")

    yield store, location


# --- Shared contract, run against the in-memory double -------------------


async def test_put_then_get_round_trips(
    memory_store: ObjectStore, location: StorageLocation
) -> None:
    key = generate_object_key(TENANT, filename="a.txt")
    await memory_store.put(location, key, b"hello", content_type="text/plain")

    assert await memory_store.get(location, key) == b"hello"


async def test_head_reports_size_and_type(
    memory_store: ObjectStore, location: StorageLocation
) -> None:
    key = generate_object_key(TENANT)
    await memory_store.put(location, key, b"12345", content_type="text/plain")

    info = await memory_store.head(location, key)

    assert info.size_bytes == 5
    assert info.content_type == "text/plain"


async def test_exists_reflects_presence(
    memory_store: ObjectStore, location: StorageLocation
) -> None:
    key = generate_object_key(TENANT)

    assert await memory_store.exists(location, key) is False
    await memory_store.put(location, key, b"x")
    assert await memory_store.exists(location, key) is True


async def test_delete_reports_whether_it_removed_anything(
    memory_store: ObjectStore, location: StorageLocation
) -> None:
    key = generate_object_key(TENANT)
    await memory_store.put(location, key, b"x")

    assert await memory_store.delete(location, key) is True
    assert await memory_store.delete(location, key) is False


async def test_missing_object_raises(
    memory_store: ObjectStore, location: StorageLocation
) -> None:
    with pytest.raises(ObjectNotFoundError):
        await memory_store.get(location, generate_object_key(TENANT))


async def test_signed_url_is_refused_for_a_missing_object(
    memory_store: ObjectStore, location: StorageLocation
) -> None:
    with pytest.raises(ObjectNotFoundError):
        await memory_store.signed_url(
            location, generate_object_key(TENANT), expires_in=60
        )


async def test_store_rejects_an_unsafe_key(
    memory_store: ObjectStore, location: StorageLocation
) -> None:
    """Validation sits in the store too, not only at generation."""
    from app.storage.errors import InvalidObjectKeyError

    with pytest.raises(InvalidObjectKeyError):
        await memory_store.put(location, "../escape", b"x")


# --- Real MinIO ----------------------------------------------------------


async def test_minio_round_trip(
    minio_store: tuple[S3ObjectStore, StorageLocation],
) -> None:
    store, location = minio_store
    key = generate_object_key(TENANT, filename="report.pdf")

    written = await store.put(location, key, b"%PDF-1.4 fake", content_type="application/pdf")
    read_back = await store.get(location, key)

    assert read_back == b"%PDF-1.4 fake"
    assert written.size_bytes == len(b"%PDF-1.4 fake")


async def test_minio_head_reports_metadata(
    minio_store: tuple[S3ObjectStore, StorageLocation],
) -> None:
    store, location = minio_store
    key = generate_object_key(TENANT)
    await store.put(location, key, b"12345", content_type="text/plain", metadata={"a": "b"})

    info = await store.head(location, key)

    assert info.size_bytes == 5
    assert info.content_type == "text/plain"
    assert info.custom.get("a") == "b"
    assert info.etag


async def test_minio_exists_and_delete(
    minio_store: tuple[S3ObjectStore, StorageLocation],
) -> None:
    store, location = minio_store
    key = generate_object_key(TENANT)

    assert await store.exists(location, key) is False
    await store.put(location, key, b"x")
    assert await store.exists(location, key) is True
    assert await store.delete(location, key) is True
    assert await store.exists(location, key) is False


async def test_minio_missing_object_raises_not_found(
    minio_store: tuple[S3ObjectStore, StorageLocation],
) -> None:
    store, location = minio_store

    with pytest.raises(ObjectNotFoundError):
        await store.get(location, generate_object_key(TENANT))


async def test_minio_signed_url_is_usable_and_scoped(
    minio_store: tuple[S3ObjectStore, StorageLocation],
) -> None:
    """The URL must actually fetch the object, and only that object."""
    import urllib.request

    import anyio

    store, location = minio_store
    key = generate_object_key(TENANT, filename="a.txt")
    await store.put(location, key, b"signed-content", content_type="text/plain")

    url = await store.signed_url(location, key, expires_in=120)

    def _fetch() -> bytes:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            return bytes(response.read())

    assert await anyio.to_thread.run_sync(_fetch) == b"signed-content"
    assert key in url


async def test_minio_signed_url_expires(
    minio_store: tuple[S3ObjectStore, StorageLocation],
) -> None:
    """An expired URL must stop working, or it is not short-lived at all."""
    import urllib.error
    import urllib.request

    import anyio

    store, location = minio_store
    key = generate_object_key(TENANT)
    await store.put(location, key, b"x")

    url = await store.signed_url(location, key, expires_in=1)
    await anyio.sleep(2.5)

    def _fetch() -> int:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    assert await anyio.to_thread.run_sync(_fetch) == 403


async def test_minio_signed_url_forces_a_download_name(
    minio_store: tuple[S3ObjectStore, StorageLocation],
) -> None:
    """Attachment disposition is what stops stored HTML executing in-origin."""
    store, location = minio_store
    key = generate_object_key(TENANT, filename="a.html")
    await store.put(location, key, b"<script>alert(1)</script>", content_type="text/html")

    url = await store.signed_url(
        location, key, expires_in=60, download_filename="a.html"
    )

    assert "response-content-disposition" in url.lower()
    assert "attachment" in url.lower()


# --- Failure handling ----------------------------------------------------


async def test_missing_credentials_raise_a_configuration_error() -> None:
    store = S3ObjectStore(
        Settings(s3_access_key="", s3_secret_key="", s3_bucket="b")
    )

    with pytest.raises(StorageConfigurationError, match="credentials are not configured"):
        _ = store.client


async def test_unreachable_endpoint_raises_storage_unavailable() -> None:
    """A dead endpoint must not surface as a botocore traceback."""
    store = S3ObjectStore(
        Settings(
            s3_endpoint="http://127.0.0.1:1",
            s3_bucket="b",
            s3_access_key="k",
            s3_secret_key="s",
            s3_connect_timeout=1,
            s3_read_timeout=1,
        )
    )

    with pytest.raises(StorageUnavailableError):
        await store.get(StorageLocation(bucket="b", region="us-east-1"), "a/b")


async def test_storage_errors_never_quote_credentials() -> None:
    """An error that quotes its own configuration is how secrets reach logs."""
    secret = "super-secret-access-key-value"
    store = S3ObjectStore(
        Settings(
            s3_endpoint="http://127.0.0.1:1",
            s3_bucket="b",
            s3_access_key="AKIAEXAMPLEKEY",
            s3_secret_key=secret,
            s3_connect_timeout=1,
            s3_read_timeout=1,
        )
    )

    with pytest.raises(StorageUnavailableError) as caught:
        await store.get(StorageLocation(bucket="b", region="us-east-1"), "a/b")

    rendered = f"{caught.value!r} {caught.value} {caught.value.__cause__!r}"
    assert secret not in rendered
    assert "AKIAEXAMPLEKEY" not in rendered


def test_bucket_must_be_configured() -> None:
    with pytest.raises(StorageConfigurationError, match="bucket is not configured"):
        StorageLocation(bucket="", region="us-east-1")


def test_default_resolver_uses_the_configured_bucket() -> None:
    resolver = SingleBucketResolver(Settings(s3_bucket="configured-bucket"))

    assert resolver.resolve(TENANT).bucket == "configured-bucket"


def test_default_resolver_gives_every_tenant_the_same_bucket() -> None:
    """Correct while residency is unspecified -- and the A-19 seam."""
    resolver = SingleBucketResolver(Settings(s3_bucket="shared"))

    assert resolver.resolve(TENANT).bucket == resolver.resolve(uuid.uuid4()).bucket


# --- Streaming guard -----------------------------------------------------


def test_stream_reader_refuses_to_buffer_past_the_limit() -> None:
    """Size is enforced while reading, not after buffering everything."""
    import io

    with pytest.raises(ObjectTooLargeError):
        stream_to_bytes(io.BytesIO(b"x" * 5000), limit=1000)


def test_stream_reader_returns_content_within_the_limit() -> None:
    import io

    assert stream_to_bytes(io.BytesIO(b"hello"), limit=1000) == b"hello"
