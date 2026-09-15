"""Platform control plane: regions, jurisdictions and tenant residency

Revision ID: 64ecc211b408
Revises: b5a399931454
Created: 2026-09-15

Creates the platform context that P02 introduces, and seeds the two rows the
next migration depends on.

**Jurisdiction and residency are separate concerns** (Master Prompt §10).
``jurisdiction`` is a legal/business attribute the PDP consumes; ``region`` is
a physical location data occupies. Conflating them is how a platform ends up
claiming compliance it cannot demonstrate, so they are separate tables with
separate columns on ``tenant``.

``tenant.residency_mode`` is ``unrestricted`` or ``strict``. An unrestricted
tenant falls back to the default region for unassigned placements; a strict
tenant is **refused**. See ``app/platform/residency.py``.

Two seeds, both required:

``region`` — the deployment's default region, so ``unrestricted`` tenants
resolve to something real rather than to a dangling code.

``tenant`` — the platform sentinel ``00000000-0000-0000-0000-000000000000``,
which already owns unattributable audit events (failed logins against unknown
addresses). Without this row the foreign keys in the next migration would fail
on the first such record. Seeded **inactive** so it can never be authenticated
into.

No jurisdictions are seeded. A code list would be a fabrication under §35.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "64ecc211b408"
down_revision: str | None = "b5a399931454"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jurisdiction",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["jurisdiction.id"],
            name=op.f("fk_jurisdiction_parent_id_jurisdiction"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jurisdiction")),
        sa.UniqueConstraint("code", name="uq_jurisdiction_code"),
    )
    op.create_index(op.f("ix_jurisdiction_parent_id"), "jurisdiction", ["parent_id"], unique=False)
    op.create_table(
        "region",
        sa.Column("code", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_region")),
        sa.UniqueConstraint("code", name="uq_region_code"),
    )
    op.add_column("stored_object", sa.Column("region_code", sa.String(length=64), nullable=True))
    op.add_column("tenant", sa.Column("jurisdiction_id", sa.Uuid(), nullable=True))
    op.add_column(
        "tenant",
        sa.Column(
            "residency_mode", sa.String(length=16), server_default="unrestricted", nullable=False
        ),
    )
    op.add_column("tenant", sa.Column("relational_region_id", sa.Uuid(), nullable=True))
    op.add_column("tenant", sa.Column("object_storage_region_id", sa.Uuid(), nullable=True))
    op.add_column("tenant", sa.Column("processing_region_id", sa.Uuid(), nullable=True))
    op.add_column("tenant", sa.Column("backup_region_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_tenant_jurisdiction_id"), "tenant", ["jurisdiction_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_tenant_relational_region_id_region"),
        "tenant",
        "region",
        ["relational_region_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_tenant_backup_region_id_region"),
        "tenant",
        "region",
        ["backup_region_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_tenant_processing_region_id_region"),
        "tenant",
        "region",
        ["processing_region_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_tenant_jurisdiction_id_jurisdiction"),
        "tenant",
        "jurisdiction",
        ["jurisdiction_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_tenant_object_storage_region_id_region"),
        "tenant",
        "region",
        ["object_storage_region_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # The default region must exist before any tenant resolves to it.
    op.execute(
        """
        INSERT INTO region (id, code, name, is_active, created_at, updated_at)
        VALUES (gen_random_uuid(), 'local', 'Default region', true, now(), now())
        ON CONFLICT DO NOTHING;
        """
    )

    # The platform sentinel owns unattributable events and must exist before
    # the foreign keys in the next migration are applied. Inactive by design.
    op.execute(
        """
        INSERT INTO tenant (
            id, slug, name, is_active, residency_mode, created_at, updated_at
        )
        VALUES (
            '00000000-0000-0000-0000-000000000000',
            '__platform__',
            'Platform (unattributable events)',
            false,
            'unrestricted',
            now(), now()
        )
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM tenant WHERE slug = '__platform__';")
    op.execute("DELETE FROM region WHERE code = 'local';")
    op.drop_constraint(
        op.f("fk_tenant_object_storage_region_id_region"), "tenant", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_tenant_jurisdiction_id_jurisdiction"), "tenant", type_="foreignkey")
    op.drop_constraint(op.f("fk_tenant_processing_region_id_region"), "tenant", type_="foreignkey")
    op.drop_constraint(op.f("fk_tenant_backup_region_id_region"), "tenant", type_="foreignkey")
    op.drop_constraint(op.f("fk_tenant_relational_region_id_region"), "tenant", type_="foreignkey")
    op.drop_index(op.f("ix_tenant_jurisdiction_id"), table_name="tenant")
    op.drop_column("tenant", "backup_region_id")
    op.drop_column("tenant", "processing_region_id")
    op.drop_column("tenant", "object_storage_region_id")
    op.drop_column("tenant", "relational_region_id")
    op.drop_column("tenant", "residency_mode")
    op.drop_column("tenant", "jurisdiction_id")
    op.drop_column("stored_object", "region_code")
    op.drop_table("region")
    op.drop_index(op.f("ix_jurisdiction_parent_id"), table_name="jurisdiction")
    op.drop_table("jurisdiction")
