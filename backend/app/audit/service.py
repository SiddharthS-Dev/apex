"""Writing and reading audit records.

**Actor attribution cannot be forged.** Every write takes the actor as an
explicit argument, and the HTTP layer derives it from the authenticated
principal -- never from a request body, query string or header. There is no
code path by which a caller supplies who they are.

**Transaction behaviour is explicit, and there are two shapes**, because one
does not fit both cases:

:func:`record`
    Joins the caller's transaction. The audited change and its record commit
    together or not at all -- the atomicity ADR-0007 requires, and the default.
    Used for successful operations.

:func:`record_independently`
    Opens its own session and commits immediately. Used for **denials and
    failures**, where the operation is about to roll back: joining that
    transaction would roll the evidence back with it, and losing the record of
    a refused attempt is precisely the wrong failure mode.

Choosing between them is a real decision, so neither is hidden behind the
other.

**Reads are tenant-scoped.** Query helpers load inside ``tenant_scope``, so the
session guards filter them. Cross-tenant reading is a platform operation and
requires the explicit ``system_scope()`` hatch.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.actions import ActionSpec, ActorType, Outcome
from app.audit.immutability import install_audit_guards
from app.audit.models import AuditEvent
from app.audit.redaction import redact
from app.core.correlation import correlation_id_or_new, current_causation_id
from app.core.tenancy import system_scope, tenant_scope

install_audit_guards()


def _action_name(action: ActionSpec | str) -> str:
    return action.name if isinstance(action, ActionSpec) else action


def build_event(
    *,
    tenant_id: uuid.UUID,
    action: ActionSpec | str,
    outcome: Outcome | str = Outcome.SUCCESS,
    actor_type: ActorType | str = ActorType.USER,
    actor_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    resource_label: str | None = None,
    context: dict[str, Any] | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    """Construct an audit event without persisting it.

    All three free-form payloads are redacted here rather than at the call
    site, so a caller cannot forget -- and cannot opt out.
    """
    return AuditEvent(
        tenant_id=tenant_id,
        actor_type=str(actor_type),
        actor_id=actor_id,
        actor_label=actor_label,
        action=_action_name(action),
        outcome=str(outcome),
        resource_type=resource_type,
        resource_id=resource_id,
        resource_label=resource_label,
        occurred_at=occurred_at or datetime.now(UTC),
        correlation_id=correlation_id or correlation_id_or_new(),
        causation_id=causation_id or current_causation_id(),
        ip_address=ip_address,
        user_agent=user_agent,
        context=redact(context),
        before_state=redact(before_state) if before_state is not None else None,
        after_state=redact(after_state) if after_state is not None else None,
    )


async def record(session: AsyncSession, **kwargs: Any) -> AuditEvent:
    """Add an audit record to the caller's transaction.

    Does **not** commit. The caller's commit writes the change and its record
    together; a rollback discards both. That is the intended coupling for a
    successful operation -- see :func:`record_independently` for failures.
    """
    tenant_id = kwargs["tenant_id"]
    event = build_event(**kwargs)
    with tenant_scope(tenant_id):
        session.add(event)
        await session.flush()
    return event


async def record_independently(
    session_factory: async_sessionmaker[AsyncSession],
    **kwargs: Any,
) -> AuditEvent:
    """Write an audit record in its own transaction, committing immediately.

    For denials and failures, where the surrounding operation is about to roll
    back and the record must survive that rollback.
    """
    tenant_id = kwargs["tenant_id"]
    event = build_event(**kwargs)
    async with session_factory() as audit_session:
        with tenant_scope(tenant_id):
            audit_session.add(event)
            await audit_session.commit()
    return event


async def record_system_event(session: AsyncSession, **kwargs: Any) -> AuditEvent:
    """Record an event performed by the platform rather than a person.

    Forces ``actor_type`` to ``system`` so privileged activity is never
    indistinguishable from a user's in the record.
    """
    kwargs["actor_type"] = ActorType.SYSTEM
    kwargs["actor_id"] = None
    return await record(session, **kwargs)


# --- Reading -------------------------------------------------------------


def _apply_filters(
    statement: Select[tuple[AuditEvent]],
    *,
    action: str | None,
    actor_id: uuid.UUID | None,
    resource_type: str | None,
    resource_id: uuid.UUID | None,
    correlation_id: uuid.UUID | None,
    outcome: str | None,
    since: datetime | None,
    until: datetime | None,
) -> Select[tuple[AuditEvent]]:
    if action is not None:
        statement = statement.where(AuditEvent.action == action)
    if actor_id is not None:
        statement = statement.where(AuditEvent.actor_id == actor_id)
    if resource_type is not None:
        statement = statement.where(AuditEvent.resource_type == resource_type)
    if resource_id is not None:
        statement = statement.where(AuditEvent.resource_id == resource_id)
    if correlation_id is not None:
        statement = statement.where(AuditEvent.correlation_id == correlation_id)
    if outcome is not None:
        statement = statement.where(AuditEvent.outcome == outcome)
    if since is not None:
        statement = statement.where(AuditEvent.occurred_at >= since)
    if until is not None:
        statement = statement.where(AuditEvent.occurred_at <= until)
    return statement


async def list_events(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[AuditEvent]:
    """Read audit records for one tenant, newest first.

    Loaded under ``tenant_scope``, so the session guards do the filtering and
    there is no hand-written tenant predicate to get wrong.
    """
    statement = _apply_filters(
        select(AuditEvent),
        action=action,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=correlation_id,
        outcome=outcome,
        since=since,
        until=until,
    )
    statement = statement.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    statement = statement.limit(max(1, min(limit, 1000))).offset(max(0, offset))

    with tenant_scope(tenant_id):
        return (await session.execute(statement)).scalars().all()


async def get_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
) -> AuditEvent | None:
    """Read one audit record, or ``None`` if it is not this tenant's."""
    with tenant_scope(tenant_id):
        return (
            await session.execute(select(AuditEvent).where(AuditEvent.id == event_id))
        ).scalar_one_or_none()


async def trace(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    correlation_id: uuid.UUID,
) -> Sequence[AuditEvent]:
    """Every record from one correlated chain of activity, oldest first."""
    with tenant_scope(tenant_id):
        statement = (
            select(AuditEvent)
            .where(AuditEvent.correlation_id == correlation_id)
            .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
        )
        return (await session.execute(statement)).scalars().all()


async def list_events_across_tenants(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> Sequence[AuditEvent]:
    """Read audit records from every tenant.

    A platform operation, and named so it cannot be reached by accident: it
    uses ``system_scope()`` explicitly. No HTTP route exposes it, because who
    may read across tenants is an authority question (A-04).
    """
    with system_scope():
        statement = (
            select(AuditEvent)
            .order_by(AuditEvent.occurred_at.desc())
            .limit(max(1, min(limit, 1000)))
        )
        return (await session.execute(statement)).scalars().all()
