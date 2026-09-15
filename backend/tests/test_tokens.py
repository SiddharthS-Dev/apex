"""JWT issuing and validation tests. No database required."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.identity.tokens import (
    AUDIENCE,
    ISSUER,
    TokenError,
    create_access_token,
    decode_token,
)

USER_ID = uuid.UUID("11111111-0000-4000-8000-000000000001")
TENANT_ID = uuid.UUID("22222222-0000-4000-8000-000000000002")


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret="test-signing-key-not-used-anywhere-else")


def test_access_token_round_trips(settings: Settings) -> None:
    token = create_access_token(USER_ID, TENANT_ID, settings=settings)
    claims = decode_token(token, settings=settings)

    assert claims.user_id == USER_ID
    assert claims.tenant_id == TENANT_ID
    assert claims.token_type == "access"


def test_token_carries_the_tenant_claim(settings: Settings) -> None:
    """Tenant travels in the token, so isolation follows the principal."""
    token = create_access_token(USER_ID, TENANT_ID, settings=settings)
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=AUDIENCE,
        issuer=ISSUER,
    )

    assert payload["tid"] == str(TENANT_ID)


def test_token_does_not_embed_permissions(settings: Settings) -> None:
    """Permissions are read per request so revocation is immediate."""
    token = create_access_token(USER_ID, TENANT_ID, settings=settings)
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=AUDIENCE,
        issuer=ISSUER,
    )

    assert "permissions" not in payload
    assert "roles" not in payload


def test_session_id_round_trips(settings: Settings) -> None:
    session_id = uuid.uuid4()
    token = create_access_token(USER_ID, TENANT_ID, settings=settings, session_id=session_id)

    assert decode_token(token, settings=settings).session_id == session_id


# --- Rejection cases -----------------------------------------------------


def test_tampered_token_is_rejected(settings: Settings) -> None:
    token = create_access_token(USER_ID, TENANT_ID, settings=settings)
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

    with pytest.raises(TokenError):
        decode_token(tampered, settings=settings)


def test_token_signed_with_another_key_is_rejected(settings: Settings) -> None:
    other = Settings(jwt_secret="a-completely-different-signing-key-value")
    token = create_access_token(USER_ID, TENANT_ID, settings=other)

    with pytest.raises(TokenError):
        decode_token(token, settings=settings)


def test_expired_token_is_rejected(settings: Settings) -> None:
    expired = Settings(
        jwt_secret=settings.jwt_secret,
        access_token_ttl_minutes=-1,
    )
    token = create_access_token(USER_ID, TENANT_ID, settings=expired)

    with pytest.raises(TokenError):
        decode_token(token, settings=settings)


def test_token_valid_until_its_expiry(settings: Settings) -> None:
    token = create_access_token(USER_ID, TENANT_ID, settings=settings)
    claims = decode_token(token, settings=settings)

    assert claims.expires_at > datetime.now(UTC)
    assert claims.expires_at - claims.issued_at == timedelta(
        minutes=settings.access_token_ttl_minutes
    )


def test_token_for_another_audience_is_rejected(settings: Settings) -> None:
    """A token minted for a different service must not be replayable here."""
    now = datetime.now(UTC)
    foreign = jwt.encode(
        {
            "sub": str(USER_ID),
            "tid": str(TENANT_ID),
            "typ": "access",
            "iss": ISSUER,
            "aud": "some-other-service",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError):
        decode_token(foreign, settings=settings)


def test_wrong_token_type_is_rejected(settings: Settings) -> None:
    now = datetime.now(UTC)
    refreshish = jwt.encode(
        {
            "sub": str(USER_ID),
            "tid": str(TENANT_ID),
            "typ": "refresh",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError, match="Expected a access token"):
        decode_token(refreshish, settings=settings)


def test_unsigned_token_is_rejected(settings: Settings) -> None:
    """The 'alg: none' attack must not work."""
    now = datetime.now(UTC)
    unsigned = jwt.encode(
        {
            "sub": str(USER_ID),
            "tid": str(TENANT_ID),
            "typ": "access",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(TokenError):
        decode_token(unsigned, settings=settings)


def test_garbage_is_rejected(settings: Settings) -> None:
    with pytest.raises(TokenError):
        decode_token("not.a.token", settings=settings)
