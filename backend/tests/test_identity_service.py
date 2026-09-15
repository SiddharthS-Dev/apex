"""Identity service tests: authentication, RBAC and tenant isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantContextMissingError, system_scope, tenant_scope
from app.identity import permissions as perms
from app.identity import service
from app.identity.models import Permission, Role, RolePermission, User, UserSession
from app.identity.service import (
    CrossTenantAssignmentError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidSessionError,
)

PASSWORD = "an-adequately-long-password"


@pytest.fixture
async def permissions(session: AsyncSession) -> int:
    return await service.sync_permissions(session)


async def _user_with_admin_role(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    email: str,
) -> User:
    role = await service.provision_baseline_roles(session, tenant_id)
    user = await service.create_user(
        session,
        tenant_id=tenant_id,
        email=email,
        password=PASSWORD,
    )
    await service.assign_role(
        session,
        tenant_id=tenant_id,
        user_id=user.id,
        role_id=role.id,
    )
    return user


# --- Permission synchronisation -----------------------------------------


async def test_sync_permissions_creates_the_code_vocabulary(session: AsyncSession) -> None:
    written = await service.sync_permissions(session)

    codes = {p.code for p in (await session.execute(select(Permission))).scalars().all()}

    assert written == len(perms.ALL_PERMISSIONS)
    assert codes == perms.permission_codes()


async def test_sync_permissions_is_idempotent(session: AsyncSession) -> None:
    await service.sync_permissions(session)

    assert await service.sync_permissions(session) == 0


# --- Provisional roles ---------------------------------------------------


async def test_baseline_role_is_marked_provisional(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
) -> None:
    """It must be obvious this is not the business authority matrix (A-04)."""
    role = await service.provision_baseline_roles(session, tenant_a)

    assert role.is_provisional is True
    assert "PROVISIONAL" in role.description


async def test_baseline_role_provisioning_is_idempotent(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
) -> None:
    first = await service.provision_baseline_roles(session, tenant_a)
    second = await service.provision_baseline_roles(session, tenant_a)

    assert first.id == second.id


async def test_each_tenant_gets_its_own_baseline_role(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    role_a = await service.provision_baseline_roles(session, tenant_a)
    role_b = await service.provision_baseline_roles(session, tenant_b)

    assert role_a.id != role_b.id
    assert role_a.tenant_id == tenant_a
    assert role_b.tenant_id == tenant_b


# --- Authentication ------------------------------------------------------


async def test_correct_credentials_authenticate(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    user = await service.authenticate(session, email="a@example.com", password=PASSWORD)

    assert user.tenant_id == tenant_a


async def test_email_is_matched_case_insensitively(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_user(
        session, tenant_id=tenant_a, email="Mixed@Example.com", password=PASSWORD
    )

    user = await service.authenticate(session, email="  MIXED@EXAMPLE.COM ", password=PASSWORD)

    assert user.email == "mixed@example.com"


async def test_wrong_password_is_rejected(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(session, email="a@example.com", password="wrong")


async def test_unknown_user_raises_the_same_error_as_a_wrong_password(
    session: AsyncSession,
) -> None:
    """The failure must not distinguish "no such user" from "wrong password"."""
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(session, email="nobody@example.com", password=PASSWORD)


async def test_user_without_a_local_credential_cannot_password_login(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """A federated-only account has no password to match."""
    await service.create_user(
        session, tenant_id=tenant_a, email="fed@example.com", password=None
    )

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(session, email="fed@example.com", password=PASSWORD)


async def test_deactivated_user_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )
    with tenant_scope(tenant_a):
        user.is_active = False
        await session.commit()

    with pytest.raises(InactiveUserError):
        await service.authenticate(session, email="a@example.com", password=PASSWORD)


async def test_authentication_records_last_login(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    user = await service.authenticate(session, email="a@example.com", password=PASSWORD)

    assert user.last_login_at is not None


# --- Sessions and refresh -----------------------------------------------


async def test_refresh_token_is_not_stored_in_plaintext(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )
    pair = await service.issue_tokens(session, user)

    with tenant_scope(tenant_a):
        stored = (await session.execute(select(UserSession))).scalars().one()

    assert stored.token_hash != pair.refresh_token
    assert pair.refresh_token not in stored.token_hash


async def test_refresh_rotates_the_token(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )
    first = await service.issue_tokens(session, user)

    second = await service.refresh_tokens(session, first.refresh_token)

    assert second.refresh_token != first.refresh_token


async def test_replaying_a_refresh_token_fails(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """Rotation is what makes a stolen token detectable rather than silent."""
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )
    first = await service.issue_tokens(session, user)
    await service.refresh_tokens(session, first.refresh_token)

    with pytest.raises(InvalidSessionError):
        await service.refresh_tokens(session, first.refresh_token)


async def test_unknown_refresh_token_is_rejected(session: AsyncSession) -> None:
    with pytest.raises(InvalidSessionError):
        await service.refresh_tokens(session, "not-a-real-refresh-token")


async def test_expired_session_cannot_refresh(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )
    pair = await service.issue_tokens(session, user)

    with tenant_scope(tenant_a):
        stored = (await session.execute(select(UserSession))).scalars().one()
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    with pytest.raises(InvalidSessionError):
        await service.refresh_tokens(session, pair.refresh_token)


async def test_revoked_session_cannot_refresh(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )
    pair = await service.issue_tokens(session, user)

    with tenant_scope(tenant_a):
        stored = (await session.execute(select(UserSession))).scalars().one()

    assert await service.revoke_session(
        session, tenant_id=tenant_a, session_id=stored.id
    )

    with pytest.raises(InvalidSessionError):
        await service.refresh_tokens(session, pair.refresh_token)


# --- RBAC ----------------------------------------------------------------


async def test_user_without_roles_holds_no_permissions(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
) -> None:
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    held = await service.permissions_for_user(
        session, tenant_id=tenant_a, user_id=user.id
    )

    assert held == frozenset()


async def test_assigned_role_grants_its_permissions(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
) -> None:
    user = await _user_with_admin_role(session, tenant_a, "admin@example.com")

    held = await service.permissions_for_user(
        session, tenant_id=tenant_a, user_id=user.id
    )

    assert held == perms.permission_codes()


async def test_role_assignment_is_idempotent(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
) -> None:
    role = await service.provision_baseline_roles(session, tenant_a)
    user = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    first = await service.assign_role(
        session, tenant_id=tenant_a, user_id=user.id, role_id=role.id
    )
    second = await service.assign_role(
        session, tenant_id=tenant_a, user_id=user.id, role_id=role.id
    )

    assert first.id == second.id


# --- Tenant isolation ----------------------------------------------------


async def test_users_are_invisible_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await service.create_user(
        session, tenant_id=tenant_b, email="b@example.com", password=PASSWORD
    )

    with tenant_scope(tenant_a):
        found = (await session.execute(select(User))).scalars().all()

    assert found == []


async def test_roles_are_invisible_across_tenants(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await service.provision_baseline_roles(session, tenant_b)

    with tenant_scope(tenant_a):
        found = (await session.execute(select(Role))).scalars().all()

    assert found == []


async def test_reading_users_without_a_tenant_is_refused(session: AsyncSession) -> None:
    with pytest.raises(TenantContextMissingError):
        await session.execute(select(User))


async def test_cross_tenant_role_assignment_is_rejected(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """A role from another tenant is not assignable, even with a valid id."""
    role_b = await service.provision_baseline_roles(session, tenant_b)
    user_a = await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )

    with pytest.raises(CrossTenantAssignmentError):
        await service.assign_role(
            session,
            tenant_id=tenant_a,
            user_id=user_a.id,
            role_id=role_b.id,
        )


async def test_cross_tenant_user_assignment_is_rejected(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    role_a = await service.provision_baseline_roles(session, tenant_a)
    user_b = await service.create_user(
        session, tenant_id=tenant_b, email="b@example.com", password=PASSWORD
    )

    with pytest.raises(CrossTenantAssignmentError):
        await service.assign_role(
            session,
            tenant_id=tenant_a,
            user_id=user_b.id,
            role_id=role_a.id,
        )


async def test_permissions_do_not_leak_between_tenants(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Asking for tenant A's view of a tenant B user must yield nothing."""
    user_b = await _user_with_admin_role(session, tenant_b, "b@example.com")

    held = await service.permissions_for_user(
        session, tenant_id=tenant_a, user_id=user_b.id
    )

    assert held == frozenset()


async def test_system_scope_sees_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """The privileged escape hatch works, and is the only thing that does."""
    await service.create_user(
        session, tenant_id=tenant_a, email="a@example.com", password=PASSWORD
    )
    await service.create_user(
        session, tenant_id=tenant_b, email="b@example.com", password=PASSWORD
    )

    with system_scope():
        emails = {
            user.email for user in (await session.execute(select(User))).scalars().all()
        }

    assert emails == {"a@example.com", "b@example.com"}


async def test_permissions_are_global_not_tenant_scoped(
    session: AsyncSession,
    permissions: int,
) -> None:
    """The permission vocabulary is code-defined, so it needs no tenant."""
    codes = {p.code for p in (await session.execute(select(Permission))).scalars().all()}

    assert codes == perms.permission_codes()


async def test_revoking_a_role_removes_its_permissions(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
) -> None:
    role = await service.provision_baseline_roles(session, tenant_a)
    user = await _user_with_admin_role(session, tenant_a, "admin@example.com")

    assert await service.revoke_role(
        session, tenant_id=tenant_a, user_id=user.id, role_id=role.id
    )

    held = await service.permissions_for_user(
        session, tenant_id=tenant_a, user_id=user.id
    )
    assert held == frozenset()


async def test_revoking_a_role_across_tenants_does_nothing(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Tenant A must not be able to strip a role inside tenant B."""
    role_b = await service.provision_baseline_roles(session, tenant_b)
    user_b = await _user_with_admin_role(session, tenant_b, "b@example.com")

    assert not await service.revoke_role(
        session, tenant_id=tenant_a, user_id=user_b.id, role_id=role_b.id
    )

    held = await service.permissions_for_user(
        session, tenant_id=tenant_b, user_id=user_b.id
    )
    assert held == perms.permission_codes()


async def test_column_select_across_join_is_tenant_filtered(
    session: AsyncSession,
    permissions: int,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Regression: with_loader_criteria alone does not constrain a joined table.

    A statement selecting *columns* rather than entities must still be filtered,
    or a join through tenant-scoped tables reads across tenants.
    """
    await _user_with_admin_role(session, tenant_b, "b@example.com")

    with tenant_scope(tenant_a):
        rows = (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(Role, Role.id == RolePermission.role_id)
            )
        ).scalars().all()

    assert rows == []
