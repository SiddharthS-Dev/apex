"""Integration event outbox

Revision ID: b5a399931454
Revises: 6938c0210e9d
Created: 2026-09-15

Creates ``outbox_event``: integration events awaiting publication.

The table exists so that an event can be written in the **same transaction** as
the change it describes. If that transaction commits the event exists; if it
rolls back the event never existed. A broker call inside a transaction cannot
offer that, because the publish can succeed while the transaction rolls back.

Envelope fields are columns as well as JSON, because a poller filters and orders
on them and should not have to parse every row to do it.

Idempotency is unique per ``(tenant_id, idempotency_key)``, not global: two
tenants may legitimately choose the same key, and a global constraint would let
one tenant's key silently suppress another's event.

``ix_outbox_event_status_available_at`` serves the poller's only hot query --
pending rows whose backoff has elapsed.

**No event type is seeded or constrained.** The catalogue ships empty: an
integration event is a contract with systems outside APEX, and naming one before
the requirements define its payload would commit consumers to a guess.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b5a399931454"
down_revision: str | None = "6938c0210e9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_event",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("envelope_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("producer", sa.String(length=64), nullable=False),
        sa.Column("source_context", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_event")),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_event_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_outbox_event_tenant_id_idempotency_key"
        ),
    )
    op.create_index(
        "ix_outbox_event_correlation_id", "outbox_event", ["correlation_id"], unique=False
    )
    op.create_index(
        "ix_outbox_event_status_available_at",
        "outbox_event",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(op.f("ix_outbox_event_tenant_id"), "outbox_event", ["tenant_id"], unique=False)
    op.create_index(
        "ix_outbox_event_tenant_id_status", "outbox_event", ["tenant_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_event_tenant_id_status", table_name="outbox_event")
    op.drop_index(op.f("ix_outbox_event_tenant_id"), table_name="outbox_event")
    op.drop_index("ix_outbox_event_status_available_at", table_name="outbox_event")
    op.drop_index("ix_outbox_event_correlation_id", table_name="outbox_event")
    op.drop_table("outbox_event")
