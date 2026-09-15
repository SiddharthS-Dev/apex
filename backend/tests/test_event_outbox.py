"""Outbox behaviour against PostgreSQL 16.

The central property: an event exists if and only if the transaction that
produced it committed. Everything else here -- retries, dead-lettering, replay,
claiming -- is in service of delivering events that genuinely happened.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.correlation import correlation_scope
from app.core.redaction import REDACTED
from app.core.tenancy import TenantContextMissingError, system_scope, tenant_scope
from app.events import outbox
from app.events.models import OutboxEvent, OutboxStatus

WORKER = "worker-1"


async def _enqueue(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    key: str = "key-1",
    event_type: str = "example.thing.happened",
    commit: bool = True,
    **kwargs: object,
) -> OutboxEvent:
    row = await outbox.enqueue(
        session,
        tenant_id=tenant_id,
        event_type=event_type,
        idempotency_key=key,
        **kwargs,  # type: ignore[arg-type]
    )
    if commit:
        await session.commit()
    return row


# --- The transactional guarantee ----------------------------------------


async def test_commit_persists_the_event(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    await _enqueue(session, tenant_a)

    rows = await outbox.list_events(session, tenant_id=tenant_a)

    assert [r.event_type for r in rows] == ["example.thing.happened"]


async def test_rollback_leaves_no_event(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """The decisive test: an event must never outlive a failed transaction."""
    await _enqueue(session, tenant_a, commit=False)
    await session.rollback()

    assert list(await outbox.list_events(session, tenant_id=tenant_a)) == []


async def test_event_is_visible_within_its_own_transaction_before_commit(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """Enqueue flushes, so the rest of the transaction can see the row."""
    await _enqueue(session, tenant_a, commit=False)

    with tenant_scope(tenant_a):
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    assert len(rows) == 1

    await session.rollback()


async def test_a_failure_after_enqueue_discards_the_event(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """Simulates the domain operation failing after the event was written."""
    await _enqueue(session, tenant_a, commit=False)

    try:
        raise RuntimeError("domain operation failed")
    except RuntimeError:
        await session.rollback()

    assert list(await outbox.list_events(session, tenant_id=tenant_a)) == []


async def test_new_event_starts_pending_with_no_attempts(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)

    assert row.status == OutboxStatus.PENDING
    assert row.attempts == 0
    assert row.published_at is None


# --- Idempotency ---------------------------------------------------------


async def test_duplicate_key_returns_the_same_event(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    first = await _enqueue(session, tenant_a, key="same")
    second = await _enqueue(session, tenant_a, key="same")

    assert second.event_id == first.event_id
    assert len(await outbox.list_events(session, tenant_id=tenant_a)) == 1


async def test_duplicate_key_ignores_the_second_payload(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """Deterministic: the first submission wins, and says so by returning it."""
    first = await _enqueue(session, tenant_a, key="same", payload={"v": 1})
    second = await _enqueue(session, tenant_a, key="same", payload={"v": 2})

    assert second.envelope["payload"] == first.envelope["payload"] == {"v": 1}


async def test_different_keys_produce_different_events(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    first = await _enqueue(session, tenant_a, key="a")
    second = await _enqueue(session, tenant_a, key="b")

    assert first.event_id != second.event_id


async def test_the_same_key_in_two_tenants_is_two_events(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """A global constraint would let one tenant suppress another's event."""
    first = await _enqueue(session, tenant_a, key="shared")
    second = await _enqueue(session, tenant_b, key="shared")

    assert first.event_id != second.event_id


# --- Claiming ------------------------------------------------------------


async def test_claim_returns_due_events_and_marks_them_publishing(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    await _enqueue(session, tenant_a)

    claimed = await outbox.claim(session, worker_id=WORKER)

    assert len(claimed) == 1
    assert claimed[0].status == OutboxStatus.PUBLISHING
    assert claimed[0].claimed_by == WORKER
    assert claimed[0].attempts == 1


async def test_claim_does_not_return_already_claimed_events(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)

    assert list(await outbox.claim(session, worker_id="worker-2")) == []


async def test_claim_spans_tenants_because_a_publisher_serves_all(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    await _enqueue(session, tenant_a, key="a")
    await _enqueue(session, tenant_b, key="b")

    claimed = await outbox.claim(session, worker_id=WORKER)

    assert len(claimed) == 2
    assert {row.tenant_id for row in claimed} == {tenant_a, tenant_b}


async def test_claimed_envelope_still_carries_its_tenant(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """Cross-tenant claiming is about who polls, not about whose data it is."""
    await _enqueue(session, tenant_a)
    claimed = await outbox.claim(session, worker_id=WORKER)

    assert outbox.envelope_of(claimed[0]).tenant_id == tenant_a


async def test_claim_respects_the_backoff_window(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    with tenant_scope(tenant_a):
        row.available_at = datetime.now(UTC) + timedelta(minutes=5)
        await session.commit()

    assert list(await outbox.claim(session, worker_id=WORKER)) == []


async def test_claim_batch_is_bounded(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    for index in range(5):
        await _enqueue(session, tenant_a, key=f"k{index}")

    assert len(await outbox.claim(session, worker_id=WORKER, limit=2)) == 2


# --- Settling ------------------------------------------------------------


async def test_publishing_marks_the_event_published(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)

    assert await outbox.mark_published(
        session, tenant_id=tenant_a, event_id=row.event_id
    )

    settled = await outbox.get_event(session, tenant_id=tenant_a, event_id=row.event_id)
    assert settled is not None
    assert settled.status == OutboxStatus.PUBLISHED
    assert settled.published_at is not None


async def test_published_events_are_not_reclaimed(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)
    await outbox.mark_published(session, tenant_id=tenant_a, event_id=row.event_id)

    assert list(await outbox.claim(session, worker_id=WORKER)) == []


async def test_marking_published_twice_is_reported_honestly(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.mark_published(session, tenant_id=tenant_a, event_id=row.event_id)

    assert not await outbox.mark_published(
        session, tenant_id=tenant_a, event_id=row.event_id
    )


# --- Retry ---------------------------------------------------------------


async def test_transient_failure_returns_the_event_to_pending(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)

    status = await outbox.mark_failed(
        session, tenant_id=tenant_a, event_id=row.event_id, error="connection refused"
    )

    assert status == OutboxStatus.PENDING


async def test_failure_records_the_error_and_schedules_a_backoff(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)
    before = datetime.now(UTC)

    await outbox.mark_failed(
        session, tenant_id=tenant_a, event_id=row.event_id, error="boom"
    )

    settled = await outbox.get_event(session, tenant_id=tenant_a, event_id=row.event_id)
    assert settled is not None
    assert settled.last_error == "boom"
    assert settled.available_at > before
    assert settled.claimed_by is None


def test_backoff_grows_and_is_capped() -> None:
    """Unbounded doubling means an event is eventually never retried."""
    delays = [outbox.retry_delay_seconds(n) for n in range(1, 12)]

    assert delays == sorted(delays)
    assert delays[0] == outbox.DEFAULT_RETRY_BASE_SECONDS
    assert max(delays) <= outbox.DEFAULT_RETRY_CAP_SECONDS


async def test_a_retried_event_is_claimable_once_its_backoff_elapses(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)
    await outbox.mark_failed(
        session, tenant_id=tenant_a, event_id=row.event_id, error="boom"
    )

    later = datetime.now(UTC) + timedelta(hours=2)
    assert len(await outbox.claim(session, worker_id=WORKER, now=later)) == 1


# --- Dead lettering ------------------------------------------------------


async def test_exhausting_attempts_dead_letters(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    later = datetime.now(UTC)

    status = ""
    for _ in range(outbox.DEFAULT_MAX_ATTEMPTS):
        later += timedelta(hours=2)
        await outbox.claim(session, worker_id=WORKER, now=later)
        status = await outbox.mark_failed(
            session,
            tenant_id=tenant_a,
            event_id=row.event_id,
            error="still failing",
            now=later,
        )

    assert status == OutboxStatus.DEAD_LETTERED


async def test_permanent_failure_dead_letters_immediately(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """Retrying an unfixable failure just delays the alert."""
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)

    status = await outbox.mark_failed(
        session,
        tenant_id=tenant_a,
        event_id=row.event_id,
        error="transport rejected the event",
        permanent=True,
    )

    assert status == OutboxStatus.DEAD_LETTERED


async def test_dead_lettered_events_are_not_claimed(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)
    await outbox.mark_failed(
        session, tenant_id=tenant_a, event_id=row.event_id, error="x", permanent=True
    )

    later = datetime.now(UTC) + timedelta(days=1)
    assert list(await outbox.claim(session, worker_id=WORKER, now=later)) == []


async def test_dead_lettered_events_are_kept_not_deleted(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """A dead-lettered event is an operational fact someone needs to see."""
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)
    await outbox.mark_failed(
        session, tenant_id=tenant_a, event_id=row.event_id, error="x", permanent=True
    )

    settled = await outbox.get_event(session, tenant_id=tenant_a, event_id=row.event_id)
    assert settled is not None
    assert settled.dead_lettered_at is not None
    assert await outbox.count_dead_lettered(session) == 1


async def test_failing_an_unknown_event_raises(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    with pytest.raises(outbox.OutboxError):
        await outbox.mark_failed(
            session, tenant_id=tenant_a, event_id=uuid.uuid4(), error="x"
        )


# --- Stale claims --------------------------------------------------------


async def test_a_stale_claim_is_reclaimed(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """A worker that dies mid-publish must not strand the event."""
    await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id="doomed-worker")

    later = datetime.now(UTC) + timedelta(hours=1)
    assert await outbox.reclaim_stale(session, now=later) == 1
    assert len(await outbox.claim(session, worker_id=WORKER, now=later)) == 1


async def test_reclaiming_does_not_refund_the_attempt(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """Otherwise an event that kills its worker loops forever."""
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id="doomed-worker")

    later = datetime.now(UTC) + timedelta(hours=1)
    await outbox.reclaim_stale(session, now=later)

    settled = await outbox.get_event(session, tenant_id=tenant_a, event_id=row.event_id)
    assert settled is not None
    assert settled.attempts == 1


async def test_a_fresh_claim_is_not_reclaimed(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)

    assert await outbox.reclaim_stale(session) == 0


# --- Replay --------------------------------------------------------------


async def test_replay_returns_a_dead_lettered_event_to_pending(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a)
    await outbox.claim(session, worker_id=WORKER)
    await outbox.mark_failed(
        session, tenant_id=tenant_a, event_id=row.event_id, error="x", permanent=True
    )

    assert await outbox.replay(session, tenant_id=tenant_a, event_id=row.event_id)

    settled = await outbox.get_event(session, tenant_id=tenant_a, event_id=row.event_id)
    assert settled is not None
    assert settled.status == OutboxStatus.PENDING
    assert settled.attempts == 0
    assert settled.dead_lettered_at is None


async def test_replay_keeps_the_event_id(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    """A new id would defeat the de-duplication replay depends on."""
    row = await _enqueue(session, tenant_a)
    original = row.event_id
    await outbox.replay(session, tenant_id=tenant_a, event_id=original)

    settled = await outbox.get_event(session, tenant_id=tenant_a, event_id=original)
    assert settled is not None
    assert settled.event_id == original
    assert outbox.envelope_of(settled).event_id == original


async def test_replaying_an_unknown_event_is_reported_honestly(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    assert not await outbox.replay(
        session, tenant_id=tenant_a, event_id=uuid.uuid4()
    )


# --- Tenant isolation ----------------------------------------------------


async def test_reading_the_outbox_without_a_tenant_is_refused(
    session: AsyncSession,
) -> None:
    with pytest.raises(TenantContextMissingError):
        await session.execute(select(OutboxEvent))


async def test_events_are_invisible_across_tenants(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    await _enqueue(session, tenant_b)

    assert list(await outbox.list_events(session, tenant_id=tenant_a)) == []


async def test_knowing_the_event_id_is_not_enough(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_b)

    assert (
        await outbox.get_event(session, tenant_id=tenant_a, event_id=row.event_id)
        is None
    )


async def test_cross_tenant_settlement_is_refused(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_b)

    assert not await outbox.mark_published(
        session, tenant_id=tenant_a, event_id=row.event_id
    )
    with pytest.raises(outbox.OutboxError):
        await outbox.mark_failed(
            session, tenant_id=tenant_a, event_id=row.event_id, error="x"
        )


async def test_cross_tenant_replay_is_refused(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_b)

    assert not await outbox.replay(
        session, tenant_id=tenant_a, event_id=row.event_id
    )


async def test_system_scope_sees_every_tenants_events(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    await _enqueue(session, tenant_a, key="a")
    await _enqueue(session, tenant_b, key="b")

    with system_scope():
        rows = (await session.execute(select(OutboxEvent))).scalars().all()

    assert len(rows) == 2


async def test_isolation_holds_after_a_claim(
    session: AsyncSession, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """Claiming uses system_scope; it must not leave isolation weakened."""
    await _enqueue(session, tenant_b)
    await outbox.claim(session, worker_id=WORKER)

    assert list(await outbox.list_events(session, tenant_id=tenant_a)) == []


# --- Payload safety ------------------------------------------------------


async def test_stored_payload_is_redacted(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(
        session, tenant_a, payload={"api_key": "sk-live-secret", "id": "1"}
    )

    assert row.envelope["payload"]["api_key"] == REDACTED
    assert row.envelope["payload"]["id"] == "1"


async def test_no_secret_reaches_the_database(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    secret = "super-secret-token-value-12345"
    await _enqueue(session, tenant_a, payload={"refresh_token": secret})

    with system_scope():
        rows = (await session.execute(select(OutboxEvent))).scalars().all()

    assert all(secret not in str(row.envelope) for row in rows)


# --- Correlation ---------------------------------------------------------


async def test_correlation_is_stored_as_a_column_for_querying(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    correlation = uuid.uuid4()
    with correlation_scope(correlation):
        row = await _enqueue(session, tenant_a)

    assert row.correlation_id == correlation


async def test_causation_propagates_into_the_outbox(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    cause = uuid.uuid4()
    with correlation_scope(uuid.uuid4(), causation_id=cause):
        row = await _enqueue(session, tenant_a)

    assert row.causation_id == cause
    assert outbox.envelope_of(row).causation_id == cause


async def test_an_events_envelope_survives_a_round_trip_through_storage(
    session: AsyncSession, tenant_a: uuid.UUID
) -> None:
    row = await _enqueue(session, tenant_a, payload={"n": 1})

    envelope = outbox.envelope_of(row)

    assert envelope.event_id == row.event_id
    assert envelope.tenant_id == tenant_a
    assert envelope.payload == {"n": 1}
