"""The audit event.

Implements [ADR-0007](../../../docs/adr/0007-append-only-audit-log.md).

**Not built on ``TenantScopedBase``.** That base carries ``updated_at``, and an
``updated_at`` column on a record that can never be updated is a lie in the
schema -- it invites a reader to believe modification is expected. This model
composes the mixins it actually needs: a UUID key, the tenant discriminator,
and a single ``recorded_at``.

**Two timestamps, deliberately.** ``occurred_at`` is when the audited thing
happened; ``recorded_at`` is when the row was written, server-side. They differ
for events recorded out of band, and an unexplained gap between them is itself
worth noticing.

**Immutability is enforced in three places**, because one is not enough:

1. The service exposes no update or delete operation.
2. A session guard (:mod:`app.audit.immutability`) raises if an instance is
   flushed as dirty or deleted.
3. A database trigger rejects ``UPDATE`` and ``DELETE`` outright, so the
   guarantee survives code that bypasses the ORM entirely.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped, UUIDPrimaryKey


class AuditEvent(Base, UUIDPrimaryKey, TenantScoped):
    """One immutable record of something that happened."""

    __tablename__ = "audit_event"
    __table_args__ = (
        # Reading audit is "this tenant, newest first", almost always filtered
        # by actor, resource or correlation. These cover those paths.
        Index("ix_audit_event_tenant_id_occurred_at", "tenant_id", "occurred_at"),
        Index("ix_audit_event_tenant_id_action", "tenant_id", "action"),
        Index("ix_audit_event_tenant_id_actor_id", "tenant_id", "actor_id"),
        Index(
            "ix_audit_event_tenant_id_resource",
            "tenant_id",
            "resource_type",
            "resource_id",
        ),
        Index("ix_audit_event_correlation_id", "correlation_id"),
    )

    # --- Who -------------------------------------------------------------
    #: user, system or anonymous -- see app.audit.actions.ActorType. Stored as
    #: text rather than a PostgreSQL enum so adding a kind needs no migration.
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Null for system and anonymous actors.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    #: How the actor identified themselves at the time, e.g. an email address.
    #: Snapshotted rather than joined: the record must still read correctly
    #: after the user is renamed or removed. This is the one place
    #: denormalisation is right -- the record is of what was true then.
    actor_label: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # --- What ------------------------------------------------------------
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)

    resource_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    resource_label: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- When ------------------------------------------------------------
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # --- Tracing ---------------------------------------------------------
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    # --- Context ---------------------------------------------------------
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    #: Free-form detail, redacted on the way in by app.audit.redaction.
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: State before and after, for changes. Redacted the same way.
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditEvent {self.action} {self.outcome} "
            f"actor={self.actor_type}:{self.actor_id} at={self.occurred_at}>"
        )
