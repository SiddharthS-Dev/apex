"""Outbox operations: enqueue, claim, settle, replay.

**Enqueue joins the caller's transaction and does not commit.** That is the
whole guarantee, and the reason this function looks unfinished at first glance:
the caller's commit is what makes the event real, and the caller's rollback is
what makes it never have existed.

**Claiming uses ``FOR UPDATE SKIP LOCKED``**, so several workers can poll the
same table concurrently without coordinating and without processing the same
row twice. A crashed worker leaves a row in ``publishing``; ``reclaim_stale``
returns it to ``pending`` rather than leaving it stuck, which is why claims
record who took them and when.

**Delivery is at-least-once.** A worker can publish successfully and die before
recording that, so the event is published again on reclaim. This is inherent to
the outbox pattern and not a defect -- consumers must be idempotent, and
``event_id`` is the handle they de-duplicate on. Promising exactly-once would
require a distributed transaction with the transport, which no transport here
is required to support.

**No transport is named.** Nothing in this module knows about Kafka, SQS,
RabbitMQ or Redis. A publisher claims rows, hands the envelope to whatever it
speaks to, and settles them. Changing transports touches that publisher and
nothing here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import system_scope, tenant_scope
from app.events.envelope import EventEnvelope, EventValidationError, build_envelope
from app.events.models import OutboxEvent, OutboxStatus

#: Attempts before a row is dead-lettered.
DEFAULT_MAX_ATTEMPTS = 5

#: Backoff base. Delay is ``base * 2 ** (attempts - 1)``, capped.
DEFAULT_RETRY_BASE_SECONDS = 10
DEFAULT_RETRY_CAP_SECONDS = 3600

#: How long a claim may be held before a worker is presumed dead.
DEFAULT_CLAIM_TIMEOUT_SECONDS = 300

#: Longest error message stored. A driver error can be enormous.
MAX_ERROR_LENGTH = 2000


class OutboxError(Exception):
    """An outbox operation failed."""


def retry_delay_seconds(
    attempts: int,
    *,
    base: int = DEFAULT_RETRY_BASE_SECONDS,
    cap: int = DEFAULT_RETRY_CAP_SECONDS,
) -> int:
    """Exponential backoff for the next attempt, capped.

    Capped because unbounded doubling means an event that failed a few times
    is effectively never retried again.
    """
    if attempts <= 0:
        return 0
    delay: int = min(cap, base * (2 ** (attempts - 1)))
    return delay


async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    event_version: int = 1,
    actor_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    source_context: str | None = None,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> OutboxEvent:
    """Add an event to the caller's transaction.

    **Does not commit.** The caller's commit publishes the event into the
    outbox atomically with whatever change it describes.

    Idempotent within a tenant: enqueuing the same ``idempotency_key`` twice
    returns the row that already exists rather than creating a second event.
    """
    envelope = build_envelope(
        tenant_id=tenant_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        payload=payload,
        event_version=event_version,
        actor_type=actor_type,
        actor_id=actor_id,
        source_context=source_context,
        correlation_id=correlation_id,
        causation_id=causation_id,
        occurred_at=occurred_at,
    )
    return await enqueue_envelope(session, envelope)


async def enqueue_envelope(
    session: AsyncSession,
    envelope: EventEnvelope,
) -> OutboxEvent:
    """Add a pre-built envelope to the caller's transaction."""
    with tenant_scope(envelope.tenant_id):
        existing = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.idempotency_key == envelope.idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        row = OutboxEvent(
            tenant_id=envelope.tenant_id,
            event_id=envelope.event_id,
            envelope_version=envelope.envelope_version,
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            occurred_at=envelope.occurred_at,
            actor_type=envelope.actor_type,
            actor_id=envelope.actor_id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            producer=envelope.producer,
            source_context=envelope.source_context,
            idempotency_key=envelope.idempotency_key,
            envelope=envelope.model_dump(mode="json"),
            status=OutboxStatus.PENDING,
            attempts=0,
            available_at=datetime.now(UTC),
        )
        session.add(row)
        # Flush rather than commit: the row must be visible to the rest of this
        # transaction, and durable only when the caller commits.
        await session.flush()
        return row


def envelope_of(row: OutboxEvent) -> EventEnvelope:
    """Rebuild the validated envelope a row carries."""
    try:
        return EventEnvelope.model_validate(row.envelope)
    except ValueError as exc:
        raise EventValidationError(
            f"Stored envelope for {row.event_id} is not valid: {exc}"
        ) from exc


# --- Delivery ------------------------------------------------------------


async def claim(
    session: AsyncSession,
    *,
    worker_id: str,
    limit: int = 20,
    now: datetime | None = None,
) -> Sequence[OutboxEvent]:
    """Claim a batch of due events for publication.

    A **platform operation**: a publisher serves every tenant, so this runs
    under ``system_scope()``. The envelope still carries the tenant, and a
    consumer must honour it -- claiming across tenants is about who polls the
    table, not about who may read the data.

    ``SKIP LOCKED`` lets several workers poll concurrently without
    coordinating; a row another worker has locked is simply passed over.
    """
    moment = now or datetime.now(UTC)

    with system_scope():
        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.PENDING,
                OutboxEvent.available_at <= moment,
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
            .limit(max(1, min(limit, 500)))
            .with_for_update(skip_locked=True)
        )
        rows = (await session.execute(statement)).scalars().all()

        for row in rows:
            row.status = OutboxStatus.PUBLISHING
            row.claimed_at = moment
            row.claimed_by = worker_id[:128]
            row.attempts += 1

        await session.commit()
        return rows


async def mark_published(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Record that an event reached the transport."""
    moment = now or datetime.now(UTC)
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_id == event_id)
            )
        ).scalar_one_or_none()
        if row is None or row.status == OutboxStatus.PUBLISHED:
            return False

        row.status = OutboxStatus.PUBLISHED
        row.published_at = moment
        row.claimed_at = None
        row.claimed_by = None
        row.last_error = None
        await session.commit()
        return True


async def mark_failed(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    error: str,
    permanent: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> str:
    """Record a failed attempt, scheduling a retry or dead-lettering.

    ``permanent=True`` dead-letters immediately, for a failure retrying cannot
    fix -- a malformed envelope, or a transport rejecting the event outright.
    Retrying those just burns attempts and delays the alert.

    Returns the resulting status.
    """
    moment = now or datetime.now(UTC)
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_id == event_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise OutboxError(f"No outbox event {event_id} for this tenant")

        row.last_error = error[:MAX_ERROR_LENGTH]
        row.claimed_at = None
        row.claimed_by = None

        if permanent or row.attempts >= max_attempts:
            row.status = OutboxStatus.DEAD_LETTERED
            row.dead_lettered_at = moment
        else:
            row.status = OutboxStatus.PENDING
            row.available_at = moment + timedelta(
                seconds=retry_delay_seconds(row.attempts)
            )

        await session.commit()
        return str(row.status)


async def reclaim_stale(
    session: AsyncSession,
    *,
    timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> int:
    """Return claims held too long to ``pending``.

    A worker that dies mid-publish leaves a row claimed forever otherwise. The
    attempt it consumed is **not** refunded, so a row that repeatedly kills its
    worker still dead-letters rather than looping indefinitely.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(seconds=timeout_seconds)

    with system_scope():
        rows = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.status == OutboxStatus.PUBLISHING,
                    OutboxEvent.claimed_at < cutoff,
                )
            )
        ).scalars().all()

        for row in rows:
            row.status = OutboxStatus.PENDING
            row.claimed_at = None
            row.claimed_by = None
            row.available_at = moment
            row.last_error = "Reclaimed after the publishing claim expired"

        await session.commit()
        return len(rows)


async def replay(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    reset_attempts: bool = True,
    now: datetime | None = None,
) -> bool:
    """Return a dead-lettered event to the pending queue.

    Safe to call on an event that was already delivered: the envelope, and
    therefore ``event_id``, is unchanged, so a consumer de-duplicating on it
    sees the replay for what it is. Replay does not mint a new event, because
    a new id would defeat exactly that.
    """
    moment = now or datetime.now(UTC)
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_id == event_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return False

        row.status = OutboxStatus.PENDING
        row.available_at = moment
        row.claimed_at = None
        row.claimed_by = None
        row.dead_lettered_at = None
        if reset_attempts:
            row.attempts = 0
        await session.commit()
        return True


# --- Reading -------------------------------------------------------------


async def get_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
) -> OutboxEvent | None:
    """Read one outbox row belonging to this tenant."""
    with tenant_scope(tenant_id):
        return (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_id == event_id)
            )
        ).scalar_one_or_none()


async def list_events(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = None,
    limit: int = 100,
) -> Sequence[OutboxEvent]:
    """List a tenant's outbox rows, newest first."""
    statement = select(OutboxEvent)
    if status is not None:
        statement = statement.where(OutboxEvent.status == status)
    statement = statement.order_by(OutboxEvent.created_at.desc()).limit(
        max(1, min(limit, 1000))
    )

    with tenant_scope(tenant_id):
        return (await session.execute(statement)).scalars().all()


async def count_dead_lettered(session: AsyncSession) -> int:
    """How many events are dead-lettered across every tenant.

    A platform health signal, not a tenant-facing one.
    """
    with system_scope():
        rows = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.status == OutboxStatus.DEAD_LETTERED
                )
            )
        ).scalars().all()
        return len(rows)
