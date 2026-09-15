"""Object storage references

Revision ID: 6938c0210e9d
Revises: 4b58a8486a79
Created: 2026-09-15

Creates ``stored_object``: where bytes live and what they hash to.

**Infrastructure, not a domain table.** It records a bucket, a key, a SHA-256,
a size and a content type. It has no title, classification, lifecycle state,
owner or relationship to anything, because those belong to the Asset model,
which needs the ontology (A-02). Assets will *reference* a stored object rather
than inherit from it, so the same bytes can back several asset versions.

``bucket`` is stored per row rather than read from configuration. If a data
residency policy (A-19) later sends new objects to a different bucket, the
existing ones remain readable rather than being stranded.

Rows are never deleted -- ``is_deleted`` is set instead -- so audit and
provenance can still resolve what a past reference pointed at after the bytes
are gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6938c0210e9d"
down_revision: str | None = "4b58a8486a79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_object",
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("storage_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deletion_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stored_object")),
        sa.UniqueConstraint("bucket", "object_key", name="uq_stored_object_bucket_object_key"),
    )
    op.create_index(
        op.f("ix_stored_object_tenant_id"), "stored_object", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_stored_object_tenant_id_content_hash",
        "stored_object",
        ["tenant_id", "content_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stored_object_tenant_id_content_hash", table_name="stored_object")
    op.drop_index(op.f("ix_stored_object_tenant_id"), table_name="stored_object")
    op.drop_table("stored_object")
