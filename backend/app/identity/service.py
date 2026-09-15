"""Identity and authorisation operations.

**Why some operations use ``system_scope()``.** Authentication happens *before*
a tenant context exists -- resolving a principal from a credential is precisely
how the tenant is discovered. Those lookups are therefore genuine
platform-level operations and use the explicit escape hatch. Every operation
after authentication runs inside ``tenant_scope()``, where the session guards
apply.

The escape hatch is used in exactly two places here: resolving a user by email
at login, and resolving a session by refresh-token hash. Both are commented at
the call site.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import system_scope, tenant_scope
from app.identity import permissions as perms
from app.identity.models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
    UserSession,
)
from app.identity.passwords import (
    equalise_timing,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from app.identity.tokens import create_access_token


class IdentityError(Exception):
    """Base class for identity failures."""


class InvalidCredentialsError(IdentityError):
    """Authentication failed.

    Deliberately undifferentiated: no such user, wrong password, and a user
    with no local credential all raise this, so the response cannot be used to
    enumerate accounts.
    """


class InactiveUserError(IdentityError):
    """The account exists but has been deactivated."""


class InvalidSessionError(IdentityError):
    """The refresh token is unknown, expired or revoked."""


class CrossTenantAssignmentError(IdentityError):
    """A role and user from different tenants cannot be linked."""


@dataclass(frozen=True, slots=True)
class TokenPair:
    """What a successful authentication returns."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: User


# --- Permission synchronisation -----------------------------------------


async def sync_permissions(session: AsyncSession) -> int:
    """Upsert the code-defined permission vocabulary into the database.

    Idempotent, so it is safe to run on every startup. Permissions are global,
    so this needs no tenant context.

    Permissions no longer present in code are **not** deleted: a role may still
    reference one, and silently dropping grants during a deploy is worse than
    leaving a row that nothing checks. Removal is a deliberate migration.
    """
    existing = {
        row.code: row for row in (await session.execute(select(Permission))).scalars().all()
    }

    written = 0
    for spec in perms.ALL_PERMISSIONS:
        current = existing.get(spec.code)
        if current is None:
            session.add(Permission(code=spec.code, description=spec.description))
            written += 1
        elif current.description != spec.description:
            current.description = spec.description
            written += 1

    if written:
        await session.commit()
    return written


async def provision_baseline_roles(session: AsyncSession, tenant_id: uuid.UUID) -> Role:
    """Create the provisional administration role for a new tenant.

    This exists so a tenant is administrable at all before the business
    authority matrix is defined (assumption **A-04**). The role is flagged
    ``is_provisional`` and described as such; it is expected to be replaced
    once requirements are available, not extended.
    """
    with tenant_scope(tenant_id):
        existing = (
            await session.execute(
                select(Role).where(Role.name == perms.TENANT_ADMIN_ROLE)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        role = Role(
            name=perms.TENANT_ADMIN_ROLE,
            description=perms.PROVISIONAL_ROLE_DESCRIPTION,
            is_provisional=True,
        )
        session.add(role)
        await session.flush()

        wanted = {spec.code for spec in perms.TENANT_ADMIN_PERMISSIONS}
        rows = (
            await session.execute(select(Permission).where(Permission.code.in_(wanted)))
        ).scalars().all()
        for permission in rows:
            session.add(RolePermission(role_id=role.id, permission_id=permission.id))

        await session.commit()
        return role


# --- Users ---------------------------------------------------------------


async def create_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    password: str | None,
    display_name: str = "",
) -> User:
    """Create a user in a tenant.

    ``password`` may be ``None`` for an account intended to authenticate
    through a federated provider once that is available.
    """
    with tenant_scope(tenant_id):
        user = User(
            email=email.strip().lower(),
            display_name=display_name,
            password_hash=hash_password(password) if password else None,
        )
        session.add(user)
        await session.commit()
        return user


async def assign_role(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
) -> UserRole:
    """Assign a role to a user within one tenant.

    Both sides are re-read under the tenant scope first. A user or role
    belonging to another tenant is simply not visible, so a cross-tenant
    assignment fails as "not found" rather than being created.
    """
    with tenant_scope(tenant_id):
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        role = (
            await session.execute(select(Role).where(Role.id == role_id))
        ).scalar_one_or_none()

        if user is None or role is None:
            raise CrossTenantAssignmentError(
                "User and role must both exist within the acting tenant."
            )

        existing = (
            await session.execute(
                select(UserRole).where(
                    UserRole.user_id == user_id,
                    UserRole.role_id == role_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        assignment = UserRole(user_id=user_id, role_id=role_id)
        session.add(assignment)
        await session.commit()
        return assignment


async def revoke_role(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
) -> bool:
    """Remove a role from a user. Returns whether anything changed.

    An assignment belonging to another tenant is not visible under this scope,
    so it cannot be revoked from outside its own tenant.
    """
    with tenant_scope(tenant_id):
        assignment = (
            await session.execute(
                select(UserRole).where(
                    UserRole.user_id == user_id,
                    UserRole.role_id == role_id,
                )
            )
        ).scalar_one_or_none()

        if assignment is None:
            return False

        await session.delete(assignment)
        await session.commit()
        return True


async def permissions_for_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> frozenset[str]:
    """Every permission code the user holds, through any of their roles.

    Read per request rather than embedded in the access token, so revoking a
    role takes effect immediately instead of at token expiry.
    """
    with tenant_scope(tenant_id):
        stmt = (
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        return frozenset((await session.execute(stmt)).scalars().all())


# --- Authentication ------------------------------------------------------


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    """Resolve and verify a principal from an email and password."""
    normalised = email.strip().lower()

    # Pre-tenant operation: the credential is what tells us which tenant the
    # principal belongs to, so the lookup cannot itself be tenant-scoped.
    with system_scope():
        user = (
            await session.execute(select(User).where(User.email == normalised))
        ).scalar_one_or_none()

    if user is None:
        # Spend comparable time so response latency does not reveal whether
        # the address exists.
        equalise_timing()
        raise InvalidCredentialsError("Invalid email or password")

    if user.password_hash is None:
        equalise_timing()
        raise InvalidCredentialsError("Invalid email or password")

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Invalid email or password")

    if not user.is_active:
        raise InactiveUserError("This account is deactivated")

    with tenant_scope(user.tenant_id):
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.last_login_at = datetime.now(UTC)
        await session.commit()

    return user


async def issue_tokens(
    session: AsyncSession,
    user: User,
    *,
    settings: Settings | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> TokenPair:
    """Mint an access/refresh pair and record the session."""
    settings = settings or get_settings()
    refresh_token = generate_refresh_token()

    with tenant_scope(user.tenant_id):
        user_session = UserSession(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )
        session.add(user_session)
        await session.commit()

        access_token = create_access_token(
            user.id,
            user.tenant_id,
            settings=settings,
            session_id=user_session.id,
        )

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=user,
    )


async def refresh_tokens(
    session: AsyncSession,
    refresh_token: str,
    *,
    settings: Settings | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> TokenPair:
    """Exchange a refresh token for a new pair, rotating the old one.

    The presented token is revoked as part of the exchange. Replaying it -- for
    example after it was stolen -- then fails, which is what makes theft
    detectable rather than silent.
    """
    settings = settings or get_settings()
    token_hash = hash_refresh_token(refresh_token)

    # Pre-tenant operation: an opaque refresh token carries no tenant, so the
    # session it names has to be resolved before scope can be established.
    with system_scope():
        user_session = (
            await session.execute(
                select(UserSession).where(UserSession.token_hash == token_hash)
            )
        ).scalar_one_or_none()

        if user_session is None:
            raise InvalidSessionError("Unknown refresh token")

        tenant_id = user_session.tenant_id
        user = user_session.user

    now = datetime.now(UTC)
    if user_session.revoked_at is not None:
        raise InvalidSessionError("This session has been revoked")
    if user_session.expires_at <= now:
        raise InvalidSessionError("This session has expired")
    if not user.is_active:
        raise InactiveUserError("This account is deactivated")

    with tenant_scope(tenant_id):
        user_session.revoked_at = now
        await session.commit()

    return await issue_tokens(
        session,
        user,
        settings=settings,
        user_agent=user_agent,
        ip_address=ip_address,
    )


async def revoke_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
) -> bool:
    """Revoke one session. Returns whether anything changed."""
    with tenant_scope(tenant_id):
        user_session = (
            await session.execute(
                select(UserSession).where(UserSession.id == session_id)
            )
        ).scalar_one_or_none()

        if user_session is None or user_session.revoked_at is not None:
            return False

        user_session.revoked_at = datetime.now(UTC)
        await session.commit()
        return True
