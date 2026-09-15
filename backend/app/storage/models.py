"""The stored-object reference.

**This is infrastructure, not a domain entity.** ``StoredObject`` records where
bytes live and what they hash to. It says nothing about what the bytes *mean* --
no title, no classification, no lifecycle state, no owner, no relationship to
anything. Those belong to the Asset model, which needs the ontology (A-02) and
does not exist yet.

The separation is deliberate and worth keeping: when Assets arrive they will
*reference* a stored object rather than inherit from it, so the same bytes can
back several asset versions, and deleting an asset is a governance decision
rather than a storage one.

The row is the **only** way a key is ever obtained. Nothing accepts a key from a
caller; a caller names a ``StoredObject`` by id, the row is read under tenant
scope, and the key comes from the row. That is what makes cross-tenant object
access fail as "not found" rather than as an authorisation check someone could
forget to write.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantScopedBase


class StoredObject(TenantScopedBase):
    """A reference to bytes held in object storage."""

    __tablename__ = "stored_object"
    __table_args__ = (
        UniqueConstraint("bucket", "object_key", name="uq_stored_object_bucket_object_key"),
        # Content-addressed lookup: "have we already stored these bytes for
        # this tenant?" is the question de-duplication will ask.
        Index("ix_stored_object_tenant_id_content_hash", "tenant_id", "content_hash"),
    )

    #: Which bucket the object lives in. Stored per object rather than read from
    #: config, so that a later residency policy moving new objects to a
    #: different bucket does not strand the existing ones (A-19).
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)

    #: The APEX residency region these bytes physically occupy, recorded per
    #: object rather than inferred from current configuration. Together with
    #: `bucket` this makes placement a durable fact, so a later residency change
    #: relocates new objects without stranding existing ones (A-19, A-25).
    region_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: SHA-256 of the content, hex encoded.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: What the uploader called the file. Recorded for display only -- it never
    #: reaches the storage key, because a key derived from a filename is a
    #: path-traversal primitive.
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)

    #: Free-form infrastructure metadata. Not domain metadata: no ontology
    #: attribute belongs here.
    storage_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    #: Set when the bytes have been removed from storage. The row survives so
    #: audit and provenance can still resolve what a past reference pointed at.
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<StoredObject {self.bucket}/{self.object_key} {self.size_bytes}B>"
