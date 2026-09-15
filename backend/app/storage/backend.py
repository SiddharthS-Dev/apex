"""The object store abstraction and its implementations.

Two implementations ship:

:class:`S3ObjectStore`
    Talks to any S3-compatible server -- MinIO locally, AWS S3 or equivalent in
    production. Uses **boto3 in a worker thread** rather than an async S3
    client. ``aiobotocore`` pins ``botocore`` to narrow ranges, which turns a
    routine dependency bump into a conflict; storage calls are coarse-grained
    enough that a thread hop is not the bottleneck.

:class:`InMemoryObjectStore`
    A complete implementation used by unit tests, so the service layer can be
    exercised without a running server. It is a test double, not a fallback:
    nothing selects it automatically, because silently degrading to memory
    would mean uploads that appear to succeed and vanish on restart.

**Errors never carry configuration.** botocore exceptions quote the endpoint
and sometimes the credentials; they are caught at this boundary and re-raised
as :mod:`app.storage.errors` types carrying only what is safe to log.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import anyio
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.core.config import Settings, get_settings
from app.storage.errors import (
    ObjectNotFoundError,
    StorageConfigurationError,
    StorageUnavailableError,
)
from app.storage.keys import validate_key
from app.storage.locations import StorageLocation

#: S3 error codes that mean "no such object" rather than a real failure.
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound", "NoSuchBucket"})


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """What the store knows about a stored object."""

    key: str
    bucket: str
    size_bytes: int
    content_type: str | None = None
    etag: str | None = None
    last_modified: datetime | None = None
    custom: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ObjectStore(Protocol):
    """The storage operations APEX depends on."""

    async def put(
        self,
        location: StorageLocation,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectMetadata:
        """Store bytes at a key, returning what was written."""
        ...

    async def get(self, location: StorageLocation, key: str) -> bytes:
        """Read an object's bytes."""
        ...

    async def head(self, location: StorageLocation, key: str) -> ObjectMetadata:
        """Read an object's metadata without its bytes."""
        ...

    async def exists(self, location: StorageLocation, key: str) -> bool:
        """Whether an object exists."""
        ...

    async def delete(self, location: StorageLocation, key: str) -> bool:
        """Remove an object. Returns whether it was there."""
        ...

    async def signed_url(
        self,
        location: StorageLocation,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        """A short-lived URL granting read access to one object."""
        ...

    async def ensure_bucket(self, location: StorageLocation) -> None:
        """Create the bucket if it does not exist. For development setup."""
        ...


# --- In-memory -----------------------------------------------------------


class InMemoryObjectStore:
    """A complete in-process store, for unit tests.

    Not a production fallback: an upload that appears to succeed and vanishes
    on restart is worse than an upload that fails.
    """

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], tuple[bytes, ObjectMetadata]] = {}
        self._buckets: set[str] = set()

    def _slot(self, location: StorageLocation, key: str) -> tuple[str, str]:
        return (location.bucket, validate_key(key))

    async def put(
        self,
        location: StorageLocation,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectMetadata:
        slot = self._slot(location, key)
        info = ObjectMetadata(
            key=key,
            bucket=location.bucket,
            size_bytes=len(data),
            content_type=content_type,
            etag=uuid.uuid4().hex,
            last_modified=datetime.now(UTC),
            custom=dict(metadata or {}),
        )
        self._objects[slot] = (data, info)
        self._buckets.add(location.bucket)
        return info

    async def get(self, location: StorageLocation, key: str) -> bytes:
        slot = self._slot(location, key)
        if slot not in self._objects:
            raise ObjectNotFoundError(f"No object at {key!r}")
        return self._objects[slot][0]

    async def head(self, location: StorageLocation, key: str) -> ObjectMetadata:
        slot = self._slot(location, key)
        if slot not in self._objects:
            raise ObjectNotFoundError(f"No object at {key!r}")
        return self._objects[slot][1]

    async def exists(self, location: StorageLocation, key: str) -> bool:
        return self._slot(location, key) in self._objects

    async def delete(self, location: StorageLocation, key: str) -> bool:
        return self._objects.pop(self._slot(location, key), None) is not None

    async def signed_url(
        self,
        location: StorageLocation,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        slot = self._slot(location, key)
        if slot not in self._objects:
            raise ObjectNotFoundError(f"No object at {key!r}")
        expiry = int(datetime.now(UTC).timestamp()) + expires_in
        return f"memory://{location.bucket}/{key}?expires={expiry}"

    async def ensure_bucket(self, location: StorageLocation) -> None:
        self._buckets.add(location.bucket)


# --- S3 ------------------------------------------------------------------


class S3ObjectStore:
    """An S3-compatible store, driven by boto3 in a worker thread."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None

    def _build_client(self) -> Any:
        settings = self._settings
        if not settings.s3_access_key or not settings.s3_secret_key:
            raise StorageConfigurationError(
                "Object storage credentials are not configured "
                "(set APEX_S3_ACCESS_KEY and APEX_S3_SECRET_KEY)"
            )

        import boto3

        config = BotoConfig(
            region_name=settings.s3_region,
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
            connect_timeout=settings.s3_connect_timeout,
            read_timeout=settings.s3_read_timeout,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        return boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=config,
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def _call(self, name: str, **kwargs: Any) -> Any:
        """Run one boto3 operation off the event loop, translating failures."""

        def _invoke() -> Any:
            return getattr(self.client, name)(**kwargs)

        try:
            return await anyio.to_thread.run_sync(_invoke)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _NOT_FOUND_CODES:
                raise ObjectNotFoundError("No such object") from None
            # `from None`: a botocore exception carries the endpoint and request
            # context, which must not reach a log through an exception chain.
            raise StorageUnavailableError(f"Storage rejected the request ({code})") from None
        except NoCredentialsError:
            raise StorageConfigurationError(
                "Object storage credentials are missing or unusable"
            ) from None
        except BotoCoreError as exc:
            raise StorageUnavailableError(
                f"Storage is unreachable ({type(exc).__name__})"
            ) from None

    async def put(
        self,
        location: StorageLocation,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectMetadata:
        validate_key(key)
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        if metadata:
            extra["Metadata"] = metadata

        response = await self._call(
            "put_object",
            Bucket=location.bucket,
            Key=key,
            Body=data,
            **extra,
        )
        return ObjectMetadata(
            key=key,
            bucket=location.bucket,
            size_bytes=len(data),
            content_type=content_type,
            etag=str(response.get("ETag", "")).strip('"') or None,
            last_modified=datetime.now(UTC),
            custom=dict(metadata or {}),
        )

    async def get(self, location: StorageLocation, key: str) -> bytes:
        validate_key(key)
        response = await self._call("get_object", Bucket=location.bucket, Key=key)
        body = response["Body"]
        try:
            data: bytes = await anyio.to_thread.run_sync(body.read)
            return data
        finally:
            body.close()

    async def head(self, location: StorageLocation, key: str) -> ObjectMetadata:
        validate_key(key)
        response = await self._call("head_object", Bucket=location.bucket, Key=key)
        return ObjectMetadata(
            key=key,
            bucket=location.bucket,
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType"),
            etag=str(response.get("ETag", "")).strip('"') or None,
            last_modified=response.get("LastModified"),
            custom=dict(response.get("Metadata", {})),
        )

    async def exists(self, location: StorageLocation, key: str) -> bool:
        try:
            await self.head(location, key)
        except ObjectNotFoundError:
            return False
        return True

    async def delete(self, location: StorageLocation, key: str) -> bool:
        validate_key(key)
        existed = await self.exists(location, key)
        await self._call("delete_object", Bucket=location.bucket, Key=key)
        return existed

    async def signed_url(
        self,
        location: StorageLocation,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        validate_key(key)
        params: dict[str, Any] = {"Bucket": location.bucket, "Key": key}
        if download_filename:
            # Forces a download with a sane name rather than inline rendering,
            # which is what stops a stored HTML or SVG object executing in the
            # storage origin.
            safe = download_filename.replace('"', "").replace("\\", "")
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'

        def _invoke() -> str:
            return str(
                self.client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expires_in,
                )
            )

        try:
            return await anyio.to_thread.run_sync(_invoke)
        except (BotoCoreError, ClientError):
            raise StorageUnavailableError("Could not generate a signed URL") from None

    async def ensure_bucket(self, location: StorageLocation) -> None:
        """Create the bucket if absent. Intended for development setup."""
        try:
            await self._call("head_bucket", Bucket=location.bucket)
            return
        except (ObjectNotFoundError, StorageUnavailableError):
            pass

        kwargs: dict[str, Any] = {"Bucket": location.bucket}
        if location.region and location.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": location.region}
        try:
            await self._call("create_bucket", **kwargs)
        except StorageUnavailableError:
            # Concurrent creation, or the bucket already belongs to us.
            if not await self._bucket_reachable(location):
                raise

    async def _bucket_reachable(self, location: StorageLocation) -> bool:
        try:
            await self._call("head_bucket", Bucket=location.bucket)
        except (ObjectNotFoundError, StorageUnavailableError, StorageConfigurationError):
            return False
        return True


def stream_to_bytes(stream: io.BufferedIOBase, limit: int) -> bytes:
    """Read a stream, refusing to buffer more than ``limit`` bytes."""
    from app.storage.errors import ObjectTooLargeError

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ObjectTooLargeError(f"Object exceeds the {limit} byte limit")
        chunks.append(chunk)
    return b"".join(chunks)
