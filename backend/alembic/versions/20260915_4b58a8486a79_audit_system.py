"""Audit system

Revision ID: 4b58a8486a79
Revises: d88c3b7062b1
Created: 2026-09-15

Creates ``audit_event`` and the database-level guarantee that it is append-only.

**The trigger is the real guarantee.** The service exposes no update or delete,
and a session guard refuses mutation in the ORM -- but both live in application
code, and an audit log whose immutability depends on the application is an audit
log that a migration script, a psql session or a future ORM bypass can rewrite.
The trigger below holds regardless of how the statement arrives, and regardless
of the role issuing it, including the table owner.

ADR-0007 anticipated enforcing this by granting the application role INSERT and
SELECT only. That is still worth doing in production, but it cannot be done
here: in development the application connects as the table owner, and an owner
can always be re-granted. A trigger is the enforcement that does not depend on
how roles happen to be provisioned -- recorded as assumption A-23.

There is no unique or check constraint on ``action``: the audit vocabulary is a
catalogue, not a gate. A system that refuses to record an event it does not
recognise loses exactly the events most worth having.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4b58a8486a79"
down_revision: str | None = "d88c3b7062b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("resource_label", sa.String(length=512), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
    )
    op.create_index(
        "ix_audit_event_correlation_id", "audit_event", ["correlation_id"], unique=False
    )
    op.create_index(op.f("ix_audit_event_tenant_id"), "audit_event", ["tenant_id"], unique=False)
    op.create_index(
        "ix_audit_event_tenant_id_action", "audit_event", ["tenant_id", "action"], unique=False
    )
    op.create_index(
        "ix_audit_event_tenant_id_actor_id", "audit_event", ["tenant_id", "actor_id"], unique=False
    )
    op.create_index(
        "ix_audit_event_tenant_id_occurred_at",
        "audit_event",
        ["tenant_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_event_tenant_id_resource",
        "audit_event",
        ["tenant_id", "resource_type", "resource_id"],
        unique=False,
    )

    # Append-only enforcement, independent of application code and of which
    # role issues the statement.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION apex_audit_event_immutable()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_event is append-only; % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_immutable
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION apex_audit_event_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_immutable ON audit_event;")
    op.execute("DROP FUNCTION IF EXISTS apex_audit_event_immutable();")
    op.drop_index("ix_audit_event_tenant_id_resource", table_name="audit_event")
    op.drop_index("ix_audit_event_tenant_id_occurred_at", table_name="audit_event")
    op.drop_index("ix_audit_event_tenant_id_actor_id", table_name="audit_event")
    op.drop_index("ix_audit_event_tenant_id_action", table_name="audit_event")
    op.drop_index(op.f("ix_audit_event_tenant_id"), table_name="audit_event")
    op.drop_index("ix_audit_event_correlation_id", table_name="audit_event")
    op.drop_table("audit_event")
