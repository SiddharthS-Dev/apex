"""Audit read endpoints.

Read access is authorised independently of anything else: holding
``audit.event.read`` is required, and nothing else implies it. Being able to
perform an action does not entitle you to read the record of who else
performed it.

There is **no write endpoint**. Audit records are produced by the code paths
that cause them, never posted by a client -- an API that accepts audit records
accepts forged ones.

There is also no cross-tenant read route. Who may read another tenant's audit
trail is an authority question (A-04); the capability exists in the service
layer behind ``system_scope()`` and is not exposed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.audit import service
from app.audit.schemas import AuditEventResponse
from app.identity import permissions as perms
from app.identity.dependencies import DbSession, Principal, require_permission

router = APIRouter(prefix="/audit", tags=["audit"])

CanReadAudit = Annotated[Principal, Depends(require_permission(perms.AUDIT_READ))]


@router.get("/events", response_model=list[AuditEventResponse], summary="Query audit records")
async def list_events(
    session: DbSession,
    principal: CanReadAudit,
    action: Annotated[str | None, Query()] = None,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    resource_type: Annotated[str | None, Query()] = None,
    resource_id: Annotated[uuid.UUID | None, Query()] = None,
    correlation_id: Annotated[uuid.UUID | None, Query()] = None,
    outcome: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEventResponse]:
    """Read this tenant's audit records, newest first.

    The tenant comes from the caller's token, so there is no parameter by which
    another tenant's trail could be requested.
    """
    events = await service.list_events(
        session,
        tenant_id=principal.tenant_id,
        action=action,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=correlation_id,
        outcome=outcome,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [AuditEventResponse.model_validate(event) for event in events]


@router.get(
    "/events/{event_id}",
    response_model=AuditEventResponse,
    summary="Read one audit record",
)
async def get_event(
    event_id: uuid.UUID,
    session: DbSession,
    principal: CanReadAudit,
) -> AuditEventResponse:
    """Read a single audit record belonging to the caller's tenant."""
    event = await service.get_event(
        session, tenant_id=principal.tenant_id, event_id=event_id
    )
    if event is None:
        # A record belonging to another tenant is not visible under this
        # scope, so it is genuinely "not found" rather than forbidden -- and
        # 404 does not confirm that the id exists elsewhere.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such audit event")
    return AuditEventResponse.model_validate(event)


@router.get(
    "/traces/{correlation_id}",
    response_model=list[AuditEventResponse],
    summary="Read one correlated chain of activity",
)
async def get_trace(
    correlation_id: uuid.UUID,
    session: DbSession,
    principal: CanReadAudit,
) -> list[AuditEventResponse]:
    """Every record sharing a correlation id, oldest first."""
    events = await service.trace(
        session, tenant_id=principal.tenant_id, correlation_id=correlation_id
    )
    return [AuditEventResponse.model_validate(event) for event in events]
