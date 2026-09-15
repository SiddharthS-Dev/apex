"""Identity and RBAC tables

Revision ID: 35cbdafefc75
Revises: 0001_baseline
Created: 2026-09-15

Creates the Identity context: users, roles, permissions and their assignments,
plus refresh-token sessions.

Tenancy follows ADR-0008. Every table here is tenant-scoped -- carrying a
mandatory, indexed ``tenant_id`` -- except ``permission``, which is global:
permissions are capabilities the application code enforces, not data a tenant
authors.

``app_user.email`` is unique platform-wide rather than per tenant, because
authentication has to resolve a principal before any tenant context exists
(assumption A-16).

No rows are seeded. Permissions are synchronised from code at startup, and the
provisional baseline role is created per tenant by
``app.identity.service.provision_baseline_roles`` -- there is no tenant
registry to seed against until Commit 005.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "35cbdafefc75"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )
    op.create_index(op.f("ix_app_user_tenant_id"), "app_user", ["tenant_id"], unique=False)
    op.create_table(
        "permission",
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission")),
        sa.UniqueConstraint("code", name="uq_permission_code"),
    )
    op.create_table(
        "role",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_role_tenant_id_name"),
    )
    op.create_index(op.f("ix_role_tenant_id"), "role", ["tenant_id"], unique=False)
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
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
            ["permission_id"],
            ["permission.id"],
            name=op.f("fk_role_permission_permission_id_permission"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_role_permission_role_id_role"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_permission")),
        sa.UniqueConstraint(
            "role_id", "permission_id", name="uq_role_permission_role_id_permission_id"
        ),
    )
    op.create_index(
        op.f("ix_role_permission_permission_id"), "role_permission", ["permission_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_permission_role_id"), "role_permission", ["role_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_permission_tenant_id"), "role_permission", ["tenant_id"], unique=False
    )
    op.create_table(
        "user_role",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
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
            ["role_id"], ["role.id"], name=op.f("fk_user_role_role_id_role"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_user_role_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_role")),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_role_user_id_role_id"),
    )
    op.create_index(op.f("ix_user_role_role_id"), "user_role", ["role_id"], unique=False)
    op.create_index(op.f("ix_user_role_tenant_id"), "user_role", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_user_role_user_id"), "user_role", ["user_id"], unique=False)
    op.create_table(
        "user_session",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
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
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_user_session_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_session")),
        sa.UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
    )
    op.create_index(op.f("ix_user_session_tenant_id"), "user_session", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_user_session_user_id"), "user_session", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_session_user_id"), table_name="user_session")
    op.drop_index(op.f("ix_user_session_tenant_id"), table_name="user_session")
    op.drop_table("user_session")
    op.drop_index(op.f("ix_user_role_user_id"), table_name="user_role")
    op.drop_index(op.f("ix_user_role_tenant_id"), table_name="user_role")
    op.drop_index(op.f("ix_user_role_role_id"), table_name="user_role")
    op.drop_table("user_role")
    op.drop_index(op.f("ix_role_permission_tenant_id"), table_name="role_permission")
    op.drop_index(op.f("ix_role_permission_role_id"), table_name="role_permission")
    op.drop_index(op.f("ix_role_permission_permission_id"), table_name="role_permission")
    op.drop_table("role_permission")
    op.drop_index(op.f("ix_role_tenant_id"), table_name="role")
    op.drop_table("role")
    op.drop_table("permission")
    op.drop_index(op.f("ix_app_user_tenant_id"), table_name="app_user")
    op.drop_table("app_user")
