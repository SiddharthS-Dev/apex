"""Audit writing, reading, isolation and immutability, against PostgreSQL 16."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.audit import actions
from app.audit import service as audit
from app.audit.immutability import AuditEventImmutableError
from app.audit.models import AuditEvent
from app.audit.redaction import REDACTED
from app.core.correlation import correlation_scope
from app.core.tenancy import (
    PLATFORM_TENANT_ID,
    TenantContextMissingError,
    system_scope,
    tenant_scope,
)

ACTOR = uuid.UUID("11111111-0000-4000-8000-000000000001")


async def _write(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    **overrides: object,
) -> AuditEvent:
    payload: dict[str, object] = {
        "tenant_id": tenant_id,
        "action": actions.USER_AUTHENTICATED,
        "actor_id": ACTOR,
        "actor_label": "ada@example.com",
        "resource_type": actions.ResourceType.USER,
        "resource_id": ACTOR,
    }
    payload.update(overrides)
    event = await audit.record(session, **payload)
    await session.commit()
    return event


# --- Writing -------------------------------------------------------------


async def test_event_is_recorded(session: AsyncSession, tenant_a: uuid.UUID) -> None:
    event = await _write(session, tenant_a)

    assert event.id is not None
    assert event.action == "identity.user.authenticated"
    assert event.outcome == "success"


async def test_actor_is_attributed(session: AsyncSession, tenant_a: uuid.UUID) -> None:
    event = await _write(session, tenant_a)

    assert event.actor_type == "user"
    assert event.actor_id == ACTOR
    assert event.actor_label == "ada@example.com"


async def test_actor_label_is_snapshotted_not_joined(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """The record must still read correctly after the user is renamed."""
    await _write(session, tenant_a, actor_label="old@example.com")

    with tenant_scope(tenant_a):
        stored = (await session.execute(select(AuditEvent))).scalars().one()

    assert stored.actor_label == "old@example.com"


async def test_tenant_is_stamped_from_the_argument(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    event = await _write(session, tenant_a)

    assert event.tenant_id == tenant_a


async def test_system_events_are_distinguishable(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """Privileged activity must never look like a user's."""
    event = await audit.record_system_event(
        session, tenant_id=tenant_a, action="platform.maintenance.ran"
    )
    await session.commit()

    assert event.actor_type == "system"
    assert event.actor_id is None


async def test_outcome_records_denial_distinctly(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    event = await _write(
        session,
        tenant_a,
        action=actions.PERMISSION_DENIED,
        outcome=actions.Outcome.DENIED,
    )

    assert event.outcome == "denied"


async def test_uncatalogued_actions_are_still_recorded(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """An audit system must never drop an event for lacking a vocabulary entry."""
    event = await _write(session, tenant_a, action="future.context.did_something")

    assert event.action == "future.context.did_something"
    assert actions.is_catalogued("future.context.did_something") is False


async def test_both_timestamps_are_set(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    earlier = datetime.now(UTC) - timedelta(hours=2)
    await _write(session, tenant_a, occurred_at=earlier)

    with tenant_scope(tenant_a):
        stored = (await session.execute(select(AuditEvent))).scalars().one()

    assert stored.occurred_at < stored.recorded_at


# --- Redaction on write --------------------------------------------------


async def test_credentials_are_never_persisted(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await _write(
        session,
        tenant_a,
        context={"password": "hunter2", "note": "kept"},
        after_state={"refresh_token": "secret-value"},
    )

    with tenant_scope(tenant_a):
        stored = (await session.execute(select(AuditEvent))).scalars().one()

    assert stored.context["password"] == REDACTED
    assert stored.context["note"] == "kept"
    assert stored.after_state is not None
    assert stored.after_state["refresh_token"] == REDACTED


async def test_caller_cannot_opt_out_of_redaction(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """Redaction is applied inside the writer, not at the call site."""
    event = audit.build_event(
        tenant_id=tenant_a, action="x", context={"api_key": "sk-live"}
    )

    assert event.context["api_key"] == REDACTED


async def test_absent_state_stays_null(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """Absent and empty must remain distinguishable."""
    event = await _write(session, tenant_a)

    assert event.before_state is None
    assert event.context == {}


# --- Correlation ---------------------------------------------------------


async def test_correlation_id_is_taken_from_scope(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    correlation = uuid.uuid4()

    with correlation_scope(correlation):
        event = await _write(session, tenant_a)

    assert event.correlation_id == correlation


async def test_correlation_id_is_generated_when_unset(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """A record with an isolated id beats a record with none."""
    event = await _write(session, tenant_a)

    assert event.correlation_id is not None


async def test_causation_id_is_carried(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    cause = uuid.uuid4()

    with correlation_scope(uuid.uuid4(), causation_id=cause):
        event = await _write(session, tenant_a)

    assert event.causation_id == cause


async def test_trace_returns_one_chain_in_order(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    correlation = uuid.uuid4()
    with correlation_scope(correlation):
        await _write(session, tenant_a, action="first", occurred_at=datetime.now(UTC))
        await _write(
            session,
            tenant_a,
            action="second",
            occurred_at=datetime.now(UTC) + timedelta(seconds=1),
        )
    await _write(session, tenant_a, action="unrelated")

    chain = await audit.trace(
        session, tenant_id=tenant_a, correlation_id=correlation
    )

    assert [e.action for e in chain] == ["first", "second"]


# --- Transaction behaviour ----------------------------------------------


async def test_record_joins_the_callers_transaction(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """A rollback discards the change and its record together."""
    await audit.record(session, tenant_id=tenant_a, action="identity.user.created")
    await session.rollback()

    with tenant_scope(tenant_a):
        found = (await session.execute(select(AuditEvent))).scalars().all()

    assert found == []


async def test_record_independently_survives_a_rollback(
    session: AsyncSession,
    engine: AsyncEngine,
    tenant_a: uuid.UUID,
) -> None:
    """Evidence of a refused attempt must outlive the failing operation."""
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await audit.record_independently(
        factory,
        tenant_id=tenant_a,
        action=actions.USER_AUTHENTICATION_FAILED,
        outcome=actions.Outcome.FAILURE,
        actor_type=actions.ActorType.ANONYMOUS,
    )
    await session.rollback()

    with tenant_scope(tenant_a):
        found = (await session.execute(select(AuditEvent))).scalars().all()

    assert [e.action for e in found] == ["identity.user.authentication_failed"]


# --- Immutability --------------------------------------------------------


async def test_orm_update_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    event = await _write(session, tenant_a)

    with tenant_scope(tenant_a):
        event.action = "tampered"
        with pytest.raises(AuditEventImmutableError, match="cannot be modified"):
            await session.commit()

    await session.rollback()


async def test_orm_delete_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    event = await _write(session, tenant_a)

    with tenant_scope(tenant_a):
        await session.delete(event)
        with pytest.raises(AuditEventImmutableError, match="cannot be deleted"):
            await session.commit()

    await session.rollback()


async def test_database_trigger_refuses_update_bypassing_the_orm(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """The guarantee must hold for code that never touches the ORM guard."""
    await _write(session, tenant_a)

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            text("UPDATE audit_event SET action = 'tampered'")
        )

    await session.rollback()


async def test_database_trigger_refuses_delete_bypassing_the_orm(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await _write(session, tenant_a)

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("DELETE FROM audit_event"))

    await session.rollback()


async def test_bulk_orm_update_is_refused_by_the_database(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """A bulk UPDATE skips the flush guard, so the trigger is what catches it."""
    await _write(session, tenant_a)

    with tenant_scope(tenant_a), pytest.raises(DBAPIError, match="append-only"):
        await session.execute(update(AuditEvent).values(action="tampered"))

    await session.rollback()


# --- Tenant isolation ----------------------------------------------------


async def test_reading_audit_without_a_tenant_is_refused(
    session: AsyncSession,
) -> None:
    with pytest.raises(TenantContextMissingError):
        await session.execute(select(AuditEvent))


async def test_events_are_invisible_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _write(session, tenant_b, actor_label="b@example.com")

    found = await audit.list_events(session, tenant_id=tenant_a)

    assert list(found) == []


async def test_get_event_does_not_reach_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Knowing the id must not be enough."""
    event = await _write(session, tenant_b)

    assert await audit.get_event(session, tenant_id=tenant_a, event_id=event.id) is None
    assert await audit.get_event(session, tenant_id=tenant_b, event_id=event.id) is not None


async def test_trace_does_not_reach_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    correlation = uuid.uuid4()
    with correlation_scope(correlation):
        await _write(session, tenant_b)

    chain = await audit.trace(
        session, tenant_id=tenant_a, correlation_id=correlation
    )

    assert list(chain) == []


async def test_platform_events_never_appear_in_a_tenants_trail(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """Unattributable events go to a sentinel no tenant is ever issued."""
    await _write(session, PLATFORM_TENANT_ID, action="identity.user.authentication_failed")

    assert list(await audit.list_events(session, tenant_id=tenant_a)) == []

    with system_scope():
        found = (await session.execute(select(AuditEvent))).scalars().all()
    assert len(found) == 1


async def test_system_scope_reads_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _write(session, tenant_a, action="a")
    await _write(session, tenant_b, action="b")

    found = await audit.list_events_across_tenants(session)

    assert {e.action for e in found} == {"a", "b"}


async def test_isolation_holds_after_a_system_scope_block(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _write(session, tenant_b)

    with system_scope():
        pass

    assert list(await audit.list_events(session, tenant_id=tenant_a)) == []


# --- Filtering -----------------------------------------------------------


async def test_events_can_be_filtered(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await _write(session, tenant_a, action="one", outcome=actions.Outcome.SUCCESS)
    await _write(session, tenant_a, action="two", outcome=actions.Outcome.DENIED)

    denied = await audit.list_events(session, tenant_id=tenant_a, outcome="denied")

    assert [e.action for e in denied] == ["two"]


async def test_events_are_returned_newest_first(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    now = datetime.now(UTC)
    await _write(session, tenant_a, action="older", occurred_at=now - timedelta(hours=1))
    await _write(session, tenant_a, action="newer", occurred_at=now)

    found = await audit.list_events(session, tenant_id=tenant_a)

    assert [e.action for e in found] == ["newer", "older"]


async def test_limit_is_capped(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """An unbounded audit query is a denial-of-service waiting to happen."""
    await _write(session, tenant_a)

    assert len(await audit.list_events(session, tenant_id=tenant_a, limit=100_000)) <= 1000
