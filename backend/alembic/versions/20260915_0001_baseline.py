"""Establish the migration baseline

Revision ID: 0001_baseline
Revises:
Created: 2026-09-15

This revision intentionally creates no tables.

Its purpose is to establish the revision chain and the ``alembic_version``
table, so that every subsequent migration has a parent and the framework is
verified end to end before any schema exists.

No domain tables are created here on purpose. Under the context map in
``docs/architecture/domain-boundaries.md`` each bounded context owns its own
tables and creates them in its own commit -- Identity in Commit 004, Policy
(including the tenant registry) in Commit 005, Ontology in Commit 006. Creating
them from the foundation commit would put a context's schema outside the
context that owns it.

"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: the baseline exists to anchor the revision chain."""


def downgrade() -> None:
    """No-op: nothing to undo."""
