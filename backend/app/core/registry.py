"""Model registry -- the single import that makes the full schema visible.

``Base.metadata`` only knows about models that have been imported. Alembic
autogenerate compares that metadata against the live database, so a model that
nothing imports is invisible to it: no migration is generated, and the table
silently never gets created.

This module is the one place that imports every model. Alembic imports it, and
so does anything else needing the complete schema (schema creation in tests,
introspection tooling).

**When adding a bounded context**, add its models import below. Keep the list
alphabetical and grouped by the dependency tiers described in
``docs/architecture/domain-boundaries.md``.
"""

# from app.ontology import models as ontology_models       # Commit 006
# --- Knowledge and governance -------------------------------------------
# from app.assets import models as asset_models            # Commit 007
# from app.evidence import models as evidence_models       # Commit 010
# from app.provenance import models as provenance_models   # Commit 011
# from app.governance import models as governance_models   # Commit 012
from app.audit import models as audit_models  # noqa: F401  (Commit 013)
from app.core.models import Base, GlobalBase, TenantScopedBase

# --- Foundation ---------------------------------------------------------
from app.identity import models as identity_models  # noqa: F401  (Commit 004)
from app.policy import models as policy_models  # noqa: F401  (Commit 005)

__all__ = ["Base", "GlobalBase", "TenantScopedBase"]
