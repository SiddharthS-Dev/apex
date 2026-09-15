"""Object key generation and validation.

**Keys are generated, never supplied.** A caller hands over bytes and a
filename; APEX decides where the object lives. This is the single most
important property in this module, because a key derived from user input is a
path-traversal and cross-tenant-access primitive: ``../`` segments, absolute
paths, and a key that simply names another tenant's prefix are all trivial
otherwise.

The generated key is:

    {prefix}/tenants/{tenant_id}/{yyyy}/{mm}/{uuid}{ext}

- the tenant id is in the path, so a mis-scoped read is visible in the key
  itself and bucket policies can be written per tenant later;
- the date segments keep prefixes from growing without bound, which matters for
  listing performance on very large buckets;
- the object name is a UUID, so nothing about the upload is inferable from the
  key and two uploads of the same file never collide;
- only the *extension* survives from the original filename, and only after
  validation -- the filename itself is stored as metadata, where it cannot
  affect addressing.

:func:`validate_key` exists for defence in depth: it re-checks keys read back
from the database, so a tampered or legacy row cannot address arbitrary storage.
"""

from __future__ import annotations

import posixpath
import re
import uuid
from datetime import UTC, datetime

from app.storage.errors import InvalidObjectKeyError

#: Longest key S3 accepts.
MAX_KEY_LENGTH = 1024

#: Extensions are restricted to a conservative shape -- letters and digits only.
_EXTENSION_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")

#: Characters that make a key ambiguous, unsafe, or awkward to address.
_FORBIDDEN_KEY_RE = re.compile(r"[\x00-\x1f\x7f\\]")


def safe_extension(filename: str | None) -> str:
    """Return a dot-prefixed extension from a filename, or an empty string.

    Everything else about the filename is discarded. The extension is kept only
    so an object is recognisable in a storage console, and is validated because
    even this much of a filename reaches the key.
    """
    if not filename:
        return ""

    # Take the basename under both separators: a Windows client may send a full
    # path, and posixpath alone would treat "C:\x\y.pdf" as one segment.
    candidate = filename.replace("\\", "/")
    candidate = posixpath.basename(candidate)

    _, _, extension = candidate.rpartition(".")
    if not extension or extension == candidate:
        return ""
    if not _EXTENSION_RE.match(extension):
        return ""
    return f".{extension.lower()}"


def generate_object_key(
    tenant_id: uuid.UUID,
    *,
    filename: str | None = None,
    prefix: str = "",
    now: datetime | None = None,
    object_id: uuid.UUID | None = None,
) -> str:
    """Build a storage key for a tenant's object.

    No part of the key is taken from caller input except a validated extension.
    """
    moment = now or datetime.now(UTC)
    name = f"{object_id or uuid.uuid4()}{safe_extension(filename)}"

    segments = [
        *(segment for segment in prefix.strip("/").split("/") if segment),
        "tenants",
        str(tenant_id),
        f"{moment.year:04d}",
        f"{moment.month:02d}",
        name,
    ]
    key = "/".join(segments)
    validate_key(key)
    return key


def validate_key(key: str) -> str:
    """Re-check a key before it is used to address storage.

    Applied to keys read back from the database as well as freshly generated
    ones, so a tampered row cannot reach outside its prefix.
    """
    if not key:
        raise InvalidObjectKeyError("Object key must not be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidObjectKeyError(f"Object key exceeds {MAX_KEY_LENGTH} characters")
    if _FORBIDDEN_KEY_RE.search(key):
        raise InvalidObjectKeyError("Object key contains forbidden characters")
    if key.startswith("/"):
        raise InvalidObjectKeyError("Object key must be relative")
    if "//" in key:
        raise InvalidObjectKeyError("Object key must not contain empty segments")

    segments = key.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise InvalidObjectKeyError("Object key must not contain traversal segments")
    if any(segment != segment.strip() for segment in segments):
        raise InvalidObjectKeyError("Object key segments must not be padded with spaces")

    return key


def key_belongs_to_tenant(key: str, tenant_id: uuid.UUID) -> bool:
    """Whether a key sits inside the tenant's prefix.

    A cheap structural check used as a second line behind the tenant-scoped
    database lookup -- not a substitute for it.
    """
    return f"/tenants/{tenant_id}/" in f"/{key}"
