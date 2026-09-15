"""Audit over HTTP: independent authorisation, isolation, and login auditing."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.audit.models import AuditEvent
from app.core.correlation import CORRELATION_HEADER
from app.core.tenancy import PLATFORM_TENANT_ID, system_scope, tenant_scope
from app.identity import permissions as perms
from app.identity import service
from app.identity.models import RolePermission, User

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
        f"{API}/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --- Independent authorisation ------------------------------------------


async def test_audit_read_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/audit/events")).status_code == 401


async def test_audit_read_requires_its_own_permission(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """Being able to act does not entitle you to read who else acted."""
    role = await service.provision_baseline_roles(session, tenant_a)
    with tenant_scope(tenant_a):
        grants = (
            await session.execute(select(RolePermission).where(RolePermission.role_id == role.id))
        ).scalars().all()
        for grant in grants:
            if grant.permission.code == perms.AUDIT_READ.code:
                await session.delete(grant)
        await session.commit()

    user = await service.create_user(
        session, tenant_id=tenant_a, email="admin@example.com", password=PASSWORD
    )
    await service.assign_role(
        session, tenant_id=tenant_a, user_id=user.id, role_id=role.id
    )
    tokens = await _login(client, "admin@example.com")

    # Can still list users -- that permission was not removed.
    assert (await client.get(f"{API}/identity/users", headers=_auth(tokens))).status_code == 200

    response = await client.get(f"{API}/audit/events", headers=_auth(tokens))

    assert response.status_code == 403
    assert perms.AUDIT_READ.code in response.json()["detail"]


async def test_audit_read_succeeds_with_the_permission(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.get(f"{API}/audit/events", headers=_auth(tokens))

    assert response.status_code == 200


async def test_there_is_no_audit_write_endpoint(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """An API that accepts audit records accepts forged ones."""
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.post(
        f"{API}/audit/events",
        headers=_auth(tokens),
        json={"action": "forged.event", "outcome": "success"},
    )

    assert response.status_code == 405


# --- Login auditing ------------------------------------------------------


async def test_successful_login_is_audited(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    user = await _make_admin(session, tenant_a, "admin@example.com")
    await _login(client, "admin@example.com")

    events = await audit.list_events(
        session, tenant_id=tenant_a, action="identity.user.authenticated"
    )

    assert len(events) == 1
    assert events[0].actor_id == user.id
    assert events[0].outcome == "success"


async def test_failed_login_is_audited_against_the_right_tenant(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """The response is identical to an unknown-user failure, but the record is not."""
    await _make_admin(session, tenant_a, "admin@example.com")

    response = await client.post(
        f"{API}/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert response.status_code == 401

    events = await audit.list_events(
        session, tenant_id=tenant_a, action="identity.user.authentication_failed"
    )

    assert len(events) == 1
    assert events[0].outcome == "failure"
    assert events[0].actor_type == "anonymous"
    assert events[0].actor_label == "admin@example.com"


async def test_failed_login_for_an_unknown_address_goes_to_the_platform_tenant(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """No tenant is knowable, so it must not land in an arbitrary one."""
    response = await client.post(
        f"{API}/auth/login", json={"email": "ghost@example.com", "password": "wrong"}
    )
    assert response.status_code == 401

    assert list(await audit.list_events(session, tenant_id=tenant_a)) == []

    platform = await audit.list_events(session, tenant_id=PLATFORM_TENANT_ID)
    assert [e.actor_label for e in platform] == ["ghost@example.com"]


async def test_failed_login_never_records_the_password(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")

    await client.post(
        f"{API}/auth/login",
        json={"email": "admin@example.com", "password": "super-secret-value"},
    )

    with system_scope():
        rows = (await session.execute(select(AuditEvent))).scalars().all()

    assert all("super-secret-value" not in str(row.context) for row in rows)


async def test_logout_is_audited(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    assert (await client.post(f"{API}/auth/logout", headers=_auth(tokens))).status_code == 204

    events = await audit.list_events(
        session, tenant_id=tenant_a, action="identity.session.revoked"
    )
    assert len(events) == 1


# --- Correlation ---------------------------------------------------------


async def test_response_carries_a_correlation_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert uuid.UUID(response.headers[CORRELATION_HEADER])


async def test_a_supplied_correlation_id_is_honoured(client: AsyncClient) -> None:
    supplied = str(uuid.uuid4())

    response = await client.get("/health", headers={CORRELATION_HEADER: supplied})

    assert response.headers[CORRELATION_HEADER] == supplied


async def test_a_malformed_correlation_id_is_replaced(client: AsyncClient) -> None:
    """Unvalidated caller text must not reach stored records."""
    response = await client.get("/health", headers={CORRELATION_HEADER: "'; DROP TABLE--"})

    assert uuid.UUID(response.headers[CORRELATION_HEADER])


async def test_login_audit_uses_the_requests_correlation_id(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    supplied = uuid.uuid4()

    response = await client.post(
        f"{API}/auth/login",
        json={"email": "admin@example.com", "password": PASSWORD},
        headers={CORRELATION_HEADER: str(supplied)},
    )
    assert response.status_code == 200

    chain = await audit.trace(session, tenant_id=tenant_a, correlation_id=supplied)

    assert [e.action for e in chain] == ["identity.user.authenticated"]


# --- Isolation over HTTP -------------------------------------------------


async def test_a_token_only_reads_its_own_tenants_audit(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin-a@example.com")
    await _make_admin(session, tenant_b, "admin-b@example.com")
    await _login(client, "admin-b@example.com")

    tokens = await _login(client, "admin-a@example.com")
    response = await client.get(f"{API}/audit/events", headers=_auth(tokens))

    assert response.status_code == 200
    labels = {row["actor_label"] for row in response.json()}
    assert labels == {"admin-a@example.com"}


async def test_reading_another_tenants_event_by_id_is_404(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """404 rather than 403 -- confirming the id exists would leak it."""
    await _make_admin(session, tenant_a, "admin-a@example.com")
    await _make_admin(session, tenant_b, "admin-b@example.com")
    await _login(client, "admin-b@example.com")

    with tenant_scope(tenant_b):
        other = (await session.execute(select(AuditEvent))).scalars().first()
    assert other is not None

    tokens = await _login(client, "admin-a@example.com")
    response = await client.get(f"{API}/audit/events/{other.id}", headers=_auth(tokens))

    assert response.status_code == 404


async def test_there_is_no_cross_tenant_read_route(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """Who may read across tenants is an authority question (A-04)."""
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    for path in ("/audit/events/all", "/audit/events/system", "/audit/tenants"):
        response = await client.get(f"{API}{path}", headers=_auth(tokens))
        assert response.status_code in (404, 422), path


async def test_audit_response_never_exposes_a_password_hash(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.get(f"{API}/audit/events", headers=_auth(tokens))

    assert "argon2" not in response.text
    assert "password_hash" not in response.text


async def test_roles_listing_still_works_alongside_audit(
    client: AsyncClient,
    session: AsyncSession,
    seeded: int,
    tenant_a: uuid.UUID,
) -> None:
    """Guard against the audit permission accidentally shadowing another."""
    await _make_admin(session, tenant_a, "admin@example.com")
    tokens = await _login(client, "admin@example.com")

    response = await client.get(f"{API}/identity/roles", headers=_auth(tokens))

    assert response.status_code == 200
    assert [r["name"] for r in response.json()] == [perms.TENANT_ADMIN_ROLE]
    assert all(r["is_provisional"] for r in response.json())
