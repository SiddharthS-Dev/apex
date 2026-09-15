"""Authentication and identity endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.core.tenancy import tenant_scope
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


@auth_router.post("/login", response_model=TokenResponse, summary="Exchange credentials for tokens")
async def login(payload: LoginRequest, request: Request, session: DbSession) -> TokenResponse:
    """Authenticate and issue an access/refresh pair.

    Every failure returns the same message, so the response cannot be used to
    discover which addresses are registered.
    """
    try:
        user = await service.authenticate(
            session,
            email=payload.email,
            password=payload.password,
        )
    except InvalidCredentialsError as exc:
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

    user_agent, ip_address = client_fingerprint(request)
    pair = await service.issue_tokens(
        session,
        user,
        user_agent=user_agent,
        ip_address=ip_address,
    )
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
        await service.revoke_session(
            session,
            tenant_id=principal.tenant_id,
            session_id=principal.claims.session_id,
        )
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
