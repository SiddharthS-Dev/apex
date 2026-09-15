"""Identity and RBAC models.

**Tenancy.** Users, roles and their assignments are tenant-owned and inherit
:class:`~app.core.models.TenantScopedBase`, so the session guards filter and
stamp them like any other tenant data. Permissions are the exception: they are
*capabilities defined by the application code*, not data a tenant authors, so
they are global. A tenant composes roles from the fixed permission vocabulary;
it cannot invent a permission the code does not check.

**OIDC readiness.** ``User.password_hash`` is nullable. Local password
authentication is one credential type, not the definition of a user. Adding
federated login later means adding a ``federated_identity`` table keyed on
``(provider, subject) -> user_id`` and leaving ``password_hash`` null -- no
change to ``User``, roles, assignments or any authorisation code. See
ADR-0009.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import GlobalBase, TenantScopedBase


class Permission(GlobalBase):
    """A capability the application code checks for.

    Global rather than tenant-scoped: the set of permissions is determined by
    what the code enforces, and is synchronised from
    :mod:`app.identity.permissions` at startup.
    """

    __tablename__ = "permission"
    __table_args__ = (UniqueConstraint("code", name="uq_permission_code"),)

    code: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Role(TenantScopedBase):
    """A named bundle of permissions, owned by a tenant."""

    __tablename__ = "role"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_role_tenant_id_name"),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: Marks a role created by :func:`app.identity.service.provision_baseline_roles`
    #: rather than by the organisation. Provisional roles exist so the platform
    #: is administrable before the authority matrix is defined (assumption
    #: A-04); they are not a business decision and are expected to be replaced.
    is_provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class User(TenantScopedBase):
    """A person who can authenticate.

    ``email`` is unique platform-wide, not per tenant. Authentication happens
    before a tenant context exists, so the login lookup must be able to resolve
    a principal from the credential alone (assumption **A-16**).
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("email", name="uq_app_user_email"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    #: Null for a user who authenticates through a federated provider.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class UserRole(TenantScopedBase):
    """Assignment of a role to a user."""

    __tablename__ = "user_role"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role_user_id_role_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("role.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user: Mapped[User] = relationship(back_populates="roles", lazy="joined")
    role: Mapped[Role] = relationship(lazy="joined")


class RolePermission(TenantScopedBase):
    """Grant of a permission to a role."""

    __tablename__ = "role_permission"
    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_role_permission_role_id_permission_id",
        ),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("role.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("permission.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[Role] = relationship(back_populates="permissions", lazy="joined")
    permission: Mapped[Permission] = relationship(lazy="joined")


class UserSession(TenantScopedBase):
    """A refresh-token grant, so sessions can be revoked.

    The refresh token itself is never stored -- only a hash of it, so a
    database disclosure does not hand out usable sessions. Revocation is an
    explicit timestamp rather than a delete, because the audit log
    (Commit 013) needs the history.
    """

    __tablename__ = "user_session"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    #: Recorded for the audit trail introduced in Commit 013.
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped[User] = relationship(lazy="joined")
