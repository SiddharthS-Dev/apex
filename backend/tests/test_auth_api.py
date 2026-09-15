"""API-level authentication and authorisation tests.

The central assertions here are the ones that are easy to get subtly wrong:
401 and 403 must mean different things, and a valid token for one tenant must
be useless against another tenant's data.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity import permissions as perms
from app.identity import service
from app.identity.models import User
from app.identity.tokens import create_access_token

PASSWORD = "an-adequately-long-password"
API = "/api/v1"


@pytest.fixture
async def seeded(session: AsyncSession) -> int:
    return await service.sync_permissions(session)


async def _make_admin(session: AsyncSession, tenant_id: uuid.UUID, email: str) -> User:
    role = await service.provision_baseline_roles(session, tenant_id)
    user = await service.create_user(
        session, tenant_id=tenant_id, email=email, password=PASSWORD
    )
    await service.assign_role(
        session, tenant_id=tenant_id, user_id=user.id, role_id=role.id
    )
    return user


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- Login ---------------------------------------------------------------


async def test_login_returns_a_token_pair(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")

    body = await _login(client, "admin@example.com")

    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


async def test_login_with_wrong_password_is_401(
    client: AsyncClient,
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    response = await client.post(
        f"{API}/auth/login",
        json={"email": "a@example.com", "password": "wrong"},
    )

    assert response.status_code == 401


async def test_login_failures_are_indistinguishable(
    client: AsyncClient,
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """The response must not reveal whether an address is registered."""
    await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    wrong_password = await client.post(
        f"{API}/auth/login", json={"email": "a@example.com", "password": "wrong"}
    )
    no_such_user = await client.post(
        f"{API}/auth/login", json={"email": "ghost@example.com", "password": "wrong"}
    )

    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json() == no_such_user.json()


async def test_login_response_never_contains_the_hash(
    client: AsyncClient,
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    body = await _login(client, "a@example.com")

    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert "argon2" not in str(body)
    assert PASSWORD not in str(body)


# --- 401 vs 403 ----------------------------------------------------------


async def test_protected_route_without_a_token_is_401(client: AsyncClient) -> None:
    response = await client.get(f"{API}/identity/users")

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


async def test_protected_route_with_a_garbage_token_is_401(client: AsyncClient) -> None:
    response = await client.get(
        f"{API}/identity/users",
        headers={"Authorization": "Bearer not.a.real.token"},
    )

    assert response.status_code == 401


async def test_authenticated_but_unauthorised_is_403(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """A user with no roles is known, but not allowed -- 403, not 401."""
    await service.create_user(
        session, tenant_id=tenant_a, email="plain@example.com", password=PASSWORD
    )
    tokens = await _login(client, "plain@example.com")

    response = await client.get(
        f"{API}/identity/users",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 403
    assert perms.USER_READ.code in response.json()["detail"]


async def test_authorised_user_gets_the_resource(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.get(
        f"{API}/identity/users",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 200
    assert [u["email"] for u in response.json()] == ["admin@example.com"]


async def test_user_listing_never_exposes_password_hashes(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.get(
        f"{API}/identity/users",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert "password_hash" not in response.text
    assert "argon2" not in response.text


# --- Tenant isolation over HTTP -----------------------------------------


async def test_a_token_only_reaches_its_own_tenant(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """The decisive test: tenant B's users must not appear for tenant A."""
    await _make_admin(session, tenant_a, "admin-a@example.com")
    await _make_admin(session, tenant_b, "admin-b@example.com")

    tokens = await _login(client, "admin-a@example.com")
    response = await client.get(
        f"{API}/identity/users",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 200
    assert [u["email"] for u in response.json()] == ["admin-a@example.com"]


async def test_a_forged_tenant_claim_finds_nothing(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """A token whose tenant does not match its subject resolves to no user."""
    user_a = await _make_admin(session, tenant_a, "admin-a@example.com")
    await _make_admin(session, tenant_b, "admin-b@example.com")

    forged = create_access_token(user_a.id, tenant_b)

    response = await client.get(
        f"{API}/identity/users",
        headers={"Authorization": f"Bearer {forged}"},
    )

    assert response.status_code == 401


# --- /auth/me ------------------------------------------------------------


async def test_me_reports_the_caller_and_their_permissions(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "admin@example.com"
    assert body["tenant_id"] == str(tenant_a)
    assert set(body["permissions"]) == perms.permission_codes()


async def test_revoking_a_role_takes_effect_immediately(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """Permissions are read per request, not baked into the token.

    The same access token is used throughout: what changes is the user's
    authorisation, and the effect must be immediate rather than deferred to
    token expiry.
    """
    user = await _make_admin(session, tenant_a, "admin@example.com")
    role = await service.provision_baseline_roles(session, tenant_a)
    tokens = await _login(client, "admin@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert (await client.get(f"{API}/identity/users", headers=headers)).status_code == 200

    assert await service.revoke_role(
        session, tenant_id=tenant_a, user_id=user.id, role_id=role.id
    )

    assert (await client.get(f"{API}/identity/users", headers=headers)).status_code == 403


# --- Refresh and logout --------------------------------------------------


async def test_refresh_returns_a_new_pair(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.post(
        f"{API}/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert response.status_code == 200
    assert response.json()["refresh_token"] != tokens["refresh_token"]


async def test_replayed_refresh_token_is_401(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")
    await client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    replay = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert replay.status_code == 401


async def test_logout_invalidates_the_access_token(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """Logout must take effect now, not when the access token expires."""
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 200

    assert (await client.post(f"{API}/auth/logout", headers=headers)).status_code == 204

    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 401
