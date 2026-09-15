"""Tests for the declarative base and its mixins.

These need no database -- they assert the mapped schema, which is what the
migrations will be generated from.
"""

from __future__ import annotations

from app.core.models import NAMING_CONVENTION, Base
from tests.conftest import GlobalWidget, TenantWidget


def test_tenant_scoped_model_has_a_mandatory_discriminator() -> None:
    column = TenantWidget.__table__.columns["tenant_id"]

    assert column.nullable is False


def test_tenant_discriminator_is_indexed() -> None:
    """Every tenant-scoped query filters on it, so it must not be a seq scan."""
    indexed = {
        tuple(col.name for col in index.columns) for index in TenantWidget.__table__.indexes
    }

    assert ("tenant_id",) in indexed


def test_global_model_has_no_discriminator() -> None:
    """Platform-level tables are not tenant-owned."""
    assert "tenant_id" not in GlobalWidget.__table__.columns


def test_models_carry_key_and_timestamps() -> None:
    for model in (TenantWidget, GlobalWidget):
        columns = model.__table__.columns
        assert columns["id"].primary_key
        assert columns["created_at"].nullable is False
        assert columns["updated_at"].nullable is False


def test_timestamps_are_server_generated() -> None:
    """The database clock is the single source of truth for row times."""
    columns = TenantWidget.__table__.columns

    assert columns["created_at"].server_default is not None
    assert columns["updated_at"].server_default is not None


def test_constraint_naming_convention_is_applied() -> None:
    """Stable constraint names keep Alembic diffs reviewable."""
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert TenantWidget.__table__.primary_key.name == "pk__test_tenant_widget"
