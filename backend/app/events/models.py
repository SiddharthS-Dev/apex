"""The outbox table.

The outbox exists to make one guarantee true:

    **If the originating transaction commits, the event exists. If it rolls
    back, the event does not.**

That is achievable only by writing the event into the *same* transaction as the
change it describes -- which is why the event is a database row rather than a
call to a broker. A broker publish inside a transaction can succeed while the
transaction rolls back, producing an event for something that never happened;
publishing after commit can lose the event if the process dies in between.
Neither is acceptable for a platform whose purpose is provable governance.

The envelope fields are stored as columns rather than only inside the JSON
payload, because a poller needs to filter and order on them without parsing
every row.

**Ordering.** There is no ordering guarantee -- see
:mod:`app.events.envelope`. ``available_at`` orders *delivery attempts*, which
is scheduling, not sequencing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantScopedBase


class OutboxStatus(StrEnum):
    """Where an outbox row is in its lifecycle."""

    #: Waiting to be claimed. The only state a poller will pick up.
    PENDING = "pending"
    #: Claimed by a worker and in flight. Recoverable if that worker dies --
    #: see ``reclaim_stale``.
    PUBLISHING = "publishing"
    #: Handed to the transport successfully. Terminal.
    PUBLISHED = "published"
    #: Exhausted its retries or failed permanently. Terminal until replayed,
    #: and deliberately not deleted: a dead-lettered event is an operational
    #: fact someone needs to see.
    DEAD_LETTERED = "dead_lettered"


class OutboxEvent(TenantScopedBase):
    """An integration event awaiting publication."""

    __tablename__ = "outbox_event"
    __table_args__ = (
        # Idempotency is per tenant: two tenants may legitimately use the same
        # key, and a global constraint would let one tenant's key collide with
        # -- and silently suppress -- another's event.
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_outbox_event_tenant_id_idempotency_key",
        ),
        UniqueConstraint("event_id", name="uq_outbox_event_event_id"),
        # The poller's query: pending rows whose backoff has elapsed.
        Index("ix_outbox_event_status_available_at", "status", "available_at"),
        Index("ix_outbox_event_tenant_id_status", "tenant_id", "status"),
        Index("ix_outbox_event_correlation_id", "correlation_id"),
    )

    # --- Envelope --------------------------------------------------------
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    envelope_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    actor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    producer: Mapped[str] = mapped_column(String(64), nullable=False)
    source_context: Mapped[str | None] = mapped_column(String(64), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The full serialised envelope, so a consumer receives exactly what was
    #: validated rather than something reassembled from columns.
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # --- Delivery --------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Not before this time. Backoff is expressed by moving it forward.
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Which worker holds the claim, so a stuck one is identifiable.
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Why the last attempt failed. Truncated and never carrying credentials --
    #: the message comes from our own exception types, not from a driver.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<OutboxEvent {self.event_type} {self.status} "
            f"attempts={self.attempts} id={self.event_id}>"
        )
