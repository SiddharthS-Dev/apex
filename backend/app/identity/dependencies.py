"""Authentication and authorisation dependencies.

These are the explicit checks at the application boundary. A route is protected
by declaring what it requires; nothing is protected implicitly, and nothing is
left to the handler to remember.

**401 and 403 mean different things**, and the distinction is kept exact:

``401 Unauthorized``
    We do not know who you are. No token, malformed, expired, revoked session,
    or the account is gone. Always accompanied by ``WWW-Authenticate``.

``403 Forbidden``
    We know who you are, and you may not do this. Returned only after
    authentication has succeeded.

Collapsing the two -- returning 403 for an expired token, say -- makes a client
unable to tell "log in again" from "ask for access", and makes the audit trail
lie about what happened.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.tenancy import tenant_scope
from app.identity.models import User, UserSession
from app.identity.permissions import PermissionSpec
from app.identity.service import permissions_for_user
from app.identity.tokens import TokenClaims, TokenError, decode_token

#: ``auto_error=False`` so a missing header produces our own 401 with a
#: ``WWW-Authenticate`` challenge rather than FastAPI's bare 403.
bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}


def _unauthenticated(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=_UNAUTHENTICATED,
    )


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller."""

    user: User
    claims: TokenClaims

    @property
    def user_id(self) -> Any:
        return self.user.id

    @property
    def tenant_id(self) -> Any:
        return self.user.tenant_id


async def db_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped database session."""
    async for session in get_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(db_session)]
BearerToken = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_principal(credentials: BearerToken, session: DbSession) -> Principal:
    """Resolve the authenticated caller, or raise 401.

    The reason a token was rejected is never returned to the client -- only
    that it was.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthenticated()

    try:
        claims = decode_token(credentials.credentials)
    except TokenError as exc:
        raise _unauthenticated("Invalid or expired token") from exc

    # Bind the tenant from the *token*, never from anything the caller sends,
    # so tenant isolation follows the authenticated principal.
    with tenant_scope(claims.tenant_id):
        user = (
            await session.execute(select(User).where(User.id == claims.user_id))
        ).scalar_one_or_none()

        if user is None or not user.is_active:
            raise _unauthenticated("Invalid or expired token")

        if claims.session_id is not None:
            user_session = (
                await session.execute(
                    select(UserSession).where(UserSession.id == claims.session_id)
                )
            ).scalar_one_or_none()

            revoked = user_session is None or user_session.revoked_at is not None
            expired = user_session is not None and user_session.expires_at <= datetime.now(UTC)
            if revoked or expired:
                # Logout takes effect immediately rather than at token expiry.
                raise _unauthenticated("Session is no longer valid")

    return Principal(user=user, claims=claims)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_permission(
    permission: PermissionSpec | str,
) -> Callable[[Principal, AsyncSession], Coroutine[Any, Any, Principal]]:
    """Build a dependency that requires one permission.

    Permissions are read per request rather than taken from the token, so
    revoking a role takes effect immediately.
    """
    code = permission.code if isinstance(permission, PermissionSpec) else permission

    async def _check(principal: CurrentPrincipal, session: DbSession) -> Principal:
        held = await permissions_for_user(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
        if code not in held:
            # Authenticated, but not allowed -- 403, not 401.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires permission '{code}'",
            )
        return principal

    return _check


def client_fingerprint(request: Request) -> tuple[str | None, str | None]:
    """User agent and client IP, for the session record and audit trail."""
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None
    return user_agent, ip_address
