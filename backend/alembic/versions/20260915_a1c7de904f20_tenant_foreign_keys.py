"""Tenant foreign keys

Revision ID: a1c7de904f20
Revises: 64ecc211b408
Created: 2026-09-15

Adds ``tenant_id -> tenant.id`` to every tenant-owned table.

Until P02 this edge was enforced only by convention (assumption A-18). The
foreign key was omitted because ``Tenant`` lived in the Policy context, and a
key from Identity or Audit into Policy would have inverted the context map.
Relocating ``Tenant`` to the platform control plane removed that obstacle, so
the constraint the data model always implied is now enforced by the database.

``ON DELETE RESTRICT`` throughout. Deleting a tenant that owns data is refused
outright rather than cascading: audit records are immutable by design, and a
cascade that silently removed them would defeat that at the one moment it
matters most. Removing a tenant is a deliberate, staged operation, not a
``DELETE``.

Depends on the platform sentinel tenant seeded by the previous migration.
``audit_event`` already holds rows against ``00000000-0000-0000-0000-000000000000``
for unattributable authentication failures; without that row this migration
would fail on the first one.

**Production note.** These are written as plain ``ADD CONSTRAINT``, which takes
a brief lock while existing rows are validated. The tables are small at this
point in the platform's life. At scale the same change should be made as
``ADD CONSTRAINT ... NOT VALID`` followed by ``VALIDATE CONSTRAINT``, which
avoids holding the lock for the scan.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c7de904f20"
down_revision: str | None = "64ecc211b408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every table carrying the tenant discriminator.
TENANT_OWNED_TABLES: tuple[str, ...] = (
    "app_user",
    "role",
    "user_role",
    "role_permission",
    "user_session",
    "policy",
    "policy_condition",
    "audit_event",
    "stored_object",
    "outbox_event",
)


def _constraint_name(table: str) -> str:
    return f"fk_{table}_tenant_id_tenant"


def upgrade() -> None:
    for table in TENANT_OWNED_TABLES:
        op.create_foreign_key(
            _constraint_name(table),
            table,
            "tenant",
            ["tenant_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    for table in reversed(TENANT_OWNED_TABLES):
        op.drop_constraint(_constraint_name(table), table, type_="foreignkey")
