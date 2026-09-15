"""Tenant registry and ABAC policy foundation

Revision ID: d88c3b7062b1
Revises: 35cbdafefc75
Created: 2026-09-15

Creates the Policy context: the tenant registry, and policy definitions with
their conditions.

``tenant`` is global -- the registry is what defines tenants, so it cannot
itself be filtered by one. ``policy`` and ``policy_condition`` are tenant-scoped
per ADR-0008.

**No attribute semantics are encoded here.** A condition stores an attribute
key, an operator name and a JSONB array of literal operands. It does not know
what the attribute means, so data classification (A-13), audience (A-14),
geography and organisation can be added later as attribute resolvers without a
schema change.

``policy.effect`` is stored as text rather than a PostgreSQL enum type: an enum
would need a migration to add a value, and no requirement has fixed the set of
effects.

No foreign key runs from the Identity tables to ``tenant``. That would make
Identity depend on Policy and invert the context map; integrity for that edge
is enforced in the service layer instead (assumption A-18).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d88c3b7062b1"
down_revision: str | None = "35cbdafefc75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_policy_tenant_id_name"),
    )
    op.create_index(op.f("ix_policy_tenant_id"), "policy", ["tenant_id"], unique=False)
    op.create_table(
        "tenant",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant")),
        sa.UniqueConstraint("slug", name="uq_tenant_slug"),
    )
    op.create_table(
        "policy_condition",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("attribute_key", sa.String(length=128), nullable=False),
        sa.Column("operator", sa.String(length=32), nullable=False),
        sa.Column("operands", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policy.id"],
            name=op.f("fk_policy_condition_policy_id_policy"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_condition")),
    )
    op.create_index(
        op.f("ix_policy_condition_policy_id"), "policy_condition", ["policy_id"], unique=False
    )
    op.create_index(
        op.f("ix_policy_condition_tenant_id"), "policy_condition", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_policy_condition_tenant_id"), table_name="policy_condition")
    op.drop_index(op.f("ix_policy_condition_policy_id"), table_name="policy_condition")
    op.drop_table("policy_condition")
    op.drop_table("tenant")
    op.drop_index(op.f("ix_policy_tenant_id"), table_name="policy")
    op.drop_table("policy")
