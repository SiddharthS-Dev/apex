"""Application-level append-only enforcement.

The database trigger installed by the audit migration is the real guarantee --
it holds against raw SQL, a psql session, and any code that bypasses the ORM.
This guard exists in front of it for one reason: it fails at the point of the
mistake, naming the instance and the field, rather than surfacing as a
``RAISE EXCEPTION`` from Postgres at flush time with no Python context.

Defence in depth, with the two layers doing different jobs: the trigger makes
the guarantee true, and this makes a violation easy to diagnose.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent


class AuditEventImmutableError(RuntimeError):
    """An audit record was modified or deleted.

    Correcting an audit record is not possible by design. A record that was
    wrong is followed by a compensating record; the original stands.
    """


@event.listens_for(Session, "before_flush")
def _refuse_audit_mutation(
    session: Session,
    flush_context: Any,
    instances: Any,
) -> None:
    """Refuse any update or delete of an audit event."""
    for obj in session.dirty:
        if not isinstance(obj, AuditEvent):
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        changed = sorted(
            attr.key
            for attr in inspect(obj).attrs
            if attr.history.has_changes()
        )
        raise AuditEventImmutableError(
            f"Audit event {obj.id} cannot be modified (changed: {', '.join(changed)}). "
            "Record a compensating event instead."
        )

    for obj in session.deleted:
        if isinstance(obj, AuditEvent):
            raise AuditEventImmutableError(
                f"Audit event {obj.id} cannot be deleted. "
                "Retention is handled by partition archival, not by row deletion."
            )


def install_audit_guards() -> None:
    """Ensure the immutability listener is registered.

    Registration happens at import time; this exists so callers can make the
    dependency explicit and assert it in tests.
    """
    if not event.contains(Session, "before_flush", _refuse_audit_mutation):
        event.listen(Session, "before_flush", _refuse_audit_mutation)
