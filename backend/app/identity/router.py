"""Authentication and identity endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import actions as audit_actions
from app.audit import service as audit
from app.core.database import get_session_factory
from app.core.tenancy import PLATFORM_TENANT_ID, tenant_scope
from app.identity import permissions as perms
from app.identity import service
from app.identity.dependencies import (
    CurrentPrincipal,
    DbSession,
    Principal,
    client_fingerprint,
    require_permission,
)
from app.identity.models import Role, User
from app.identity.schemas import (
    CurrentUserResponse,
    LoginRequest,
    RefreshRequest,
    RoleSummary,
    TokenResponse,
    UserSummary,
)
from app.identity.service import (
    InactiveUserError,
    InvalidCredentialsError,
    InvalidSessionError,
)

auth_router = APIRouter(prefix="/auth", tags=["authentication"])
identity_router = APIRouter(prefix="/identity", tags=["identity"])

_INVALID_LOGIN = "Invalid email or password"

#: Injected rather than called directly, so that an audit write which must
#: outlive the request's transaction can still be pointed at a test database.
SessionFactory = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_session_factory)
]


@auth_router.post("/login", response_model=TokenResponse, summary="Exchange credentials for tokens")
async def login(
    payload: LoginRequest,
    request: Request,
    session: DbSession,
    session_factory: SessionFactory,
) -> TokenResponse:
    """Authenticate and issue an access/refresh pair.

    Every failure returns the same message, so the response cannot be used to
    discover which addresses are registered.
    """
    user_agent, ip_address = client_fingerprint(request)

    try:
        user = await service.authenticate(
            session,
            email=payload.email,
            password=payload.password,
        )
    except InvalidCredentialsError as exc:
        # Recorded in its own transaction: the login is failing, and evidence
        # of a refused attempt must survive that. Attributed to the resolved
        # tenant when there was one, and to the platform sentinel otherwise.
        await audit.record_independently(
            session_factory,
            tenant_id=exc.tenant_id or PLATFORM_TENANT_ID,
            action=audit_actions.USER_AUTHENTICATION_FAILED,
            outcome=audit_actions.Outcome.FAILURE,
            actor_type=audit_actions.ActorType.ANONYMOUS,
            actor_id=exc.user_id,
            actor_label=payload.email,
            resource_type=audit_actions.ResourceType.USER,
            resource_id=exc.user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            context={"reason": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_LOGIN,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is deactivated",
        ) from exc

    pair = await service.issue_tokens(
        session,
        user,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    # Success joins the request's transaction: the session row and its audit
    # record commit together.
    await audit.record(
        session,
        tenant_id=user.tenant_id,
        action=audit_actions.USER_AUTHENTICATED,
        actor_id=user.id,
        actor_label=user.email,
        resource_type=audit_actions.ResourceType.USER,
        resource_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    await session.commit()

    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@auth_router.post("/refresh", response_model=TokenResponse, summary="Rotate a refresh token")
async def refresh(payload: RefreshRequest, request: Request, session: DbSession) -> TokenResponse:
    """Exchange a refresh token for a new pair, revoking the presented one."""
    user_agent, ip_address = client_fingerprint(request)
    try:
        pair = await service.refresh_tokens(
            session,
            payload.refresh_token,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    except InvalidSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is deactivated",
        ) from exc

    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@auth_router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current session",
)
async def logout(principal: CurrentPrincipal, session: DbSession) -> Response:
    """Revoke the session the presented access token belongs to."""
    if principal.claims.session_id is not None:
        revoked = await service.revoke_session(
            session,
            tenant_id=principal.tenant_id,
            session_id=principal.claims.session_id,
        )
        if revoked:
            await audit.record(
                session,
                tenant_id=principal.tenant_id,
                action=audit_actions.SESSION_REVOKED,
                actor_id=principal.user_id,
                actor_label=principal.user.email,
                resource_type=audit_actions.ResourceType.SESSION,
                resource_id=principal.claims.session_id,
            )
            await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.get("/me", response_model=CurrentUserResponse, summary="The caller's own record")
async def me(principal: CurrentPrincipal, session: DbSession) -> CurrentUserResponse:
    """Return the authenticated user and the permissions they hold."""
    held = await service.permissions_for_user(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )
    return CurrentUserResponse(
        id=principal.user.id,
        email=principal.user.email,
        display_name=principal.user.display_name,
        is_active=principal.user.is_active,
        last_login_at=principal.user.last_login_at,
        tenant_id=principal.tenant_id,
        permissions=sorted(held),
    )


#: Declared once so the permission a route requires is visible at the route.
CanReadUsers = Annotated[Principal, Depends(require_permission(perms.USER_READ))]
CanReadRoles = Annotated[Principal, Depends(require_permission(perms.ROLE_READ))]


@identity_router.get(
    "/users",
    response_model=list[UserSummary],
    summary="List users in the tenant",
)
async def list_users(session: DbSession, principal: CanReadUsers) -> list[UserSummary]:
    """List users. Results are tenant-filtered by the session guards."""
    with tenant_scope(principal.tenant_id):
        users = (await session.execute(select(User).order_by(User.email))).scalars().all()
    return [UserSummary.model_validate(user) for user in users]


@identity_router.get(
    "/roles",
    response_model=list[RoleSummary],
    summary="List roles in the tenant",
)
async def list_roles(session: DbSession, principal: CanReadRoles) -> list[RoleSummary]:
    """List roles. Results are tenant-filtered by the session guards."""
    with tenant_scope(principal.tenant_id):
        roles = (await session.execute(select(Role).order_by(Role.name))).scalars().all()
    return [RoleSummary.model_validate(role) for role in roles]
