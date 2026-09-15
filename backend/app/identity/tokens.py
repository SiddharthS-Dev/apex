"""JWT access tokens.

The access token carries the claims the authorisation layer needs -- subject
and tenant -- so a request can be authorised without a database round trip for
identity itself. Permissions are *not* embedded: they are read per request, so
revoking a role takes effect immediately rather than at token expiry.

``tid`` (tenant) is a first-class claim. It is what
:func:`app.core.tenancy.tenant_scope` is bound to for the request, which makes
tenant isolation a property of the authenticated principal rather than of
anything the caller sends.

Tokens are signed with HS256 by default. The structure is algorithm-agnostic,
so moving to asymmetric signing -- which federated identity will want, so that
verifiers need no shared secret -- is a configuration change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import jwt

from app.core.config import Settings, get_settings

TokenType = Literal["access", "refresh"]

ACCESS: Final[TokenType] = "access"
REFRESH: Final[TokenType] = "refresh"

#: Identifies tokens minted by this platform, so a token issued for another
#: audience cannot be replayed here.
ISSUER: Final = "apex"
AUDIENCE: Final = "apex-api"


class TokenError(Exception):
    """A token was missing, malformed, expired or otherwise unusable."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Validated claims from an access token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    token_type: TokenType
    issued_at: datetime
    expires_at: datetime
    session_id: uuid.UUID | None = None


def _encode(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    token_type: TokenType,
    lifetime: timedelta,
    settings: Settings,
    session_id: uuid.UUID | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "typ": token_type,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
    }
    if session_id is not None:
        payload["sid"] = str(session_id)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    settings: Settings | None = None,
    session_id: uuid.UUID | None = None,
) -> str:
    """Mint a short-lived access token."""
    settings = settings or get_settings()
    return _encode(
        user_id=user_id,
        tenant_id=tenant_id,
        token_type=ACCESS,
        lifetime=timedelta(minutes=settings.access_token_ttl_minutes),
        settings=settings,
        session_id=session_id,
    )


def decode_token(
    token: str,
    settings: Settings | None = None,
    expected_type: TokenType | None = ACCESS,
) -> TokenClaims:
    """Verify a token's signature and claims.

    Raises :class:`TokenError` for anything that makes the token unusable --
    bad signature, expiry, wrong issuer or audience, wrong type. The caller
    turns that into a 401 without inspecting the reason, so a client cannot
    learn which check failed.
    """
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    token_type = payload.get("typ")
    if expected_type is not None and token_type != expected_type:
        raise TokenError(f"Expected a {expected_type} token, got {token_type!r}")

    try:
        user_id = uuid.UUID(payload["sub"])
        tenant_id = uuid.UUID(payload["tid"])
    except (KeyError, ValueError) as exc:
        raise TokenError("Token is missing a usable subject or tenant") from exc

    raw_session = payload.get("sid")
    try:
        session_id = uuid.UUID(raw_session) if raw_session else None
    except ValueError as exc:
        raise TokenError("Token carries a malformed session id") from exc

    return TokenClaims(
        user_id=user_id,
        tenant_id=tenant_id,
        token_type=token_type,  # type: ignore[arg-type]
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        session_id=session_id,
    )
