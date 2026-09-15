"""The permission vocabulary.

Permissions are defined in code, not by tenants, because a permission only
means something if some code path checks it. Inventing one in the database that
nothing enforces produces a role that appears to grant access and does not.

The registry below is synchronised into the ``permission`` table at startup by
:func:`app.identity.service.sync_permissions`.

**Scope note (assumption A-04).** These are *technical* permissions covering
the domains that exist today. They are deliberately not a business authority
matrix: gate authority for G0-G6, approval rights and escalation paths are
defined in Commit 012, once the requirements specifying them are available.
Adding a permission here without a code path that checks it would be exactly
the mistake described above.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class PermissionSpec(NamedTuple):
    """A permission and what it allows."""

    code: str
    description: str


# --- Identity administration --------------------------------------------
USER_READ: Final = PermissionSpec(
    "identity.user.read",
    "View users within the tenant.",
)
USER_WRITE: Final = PermissionSpec(
    "identity.user.write",
    "Create, modify and deactivate users within the tenant.",
)
ROLE_READ: Final = PermissionSpec(
    "identity.role.read",
    "View roles and their permissions within the tenant.",
)
ROLE_WRITE: Final = PermissionSpec(
    "identity.role.write",
    "Create and modify roles, and grant permissions to them.",
)
ROLE_ASSIGN: Final = PermissionSpec(
    "identity.role.assign",
    "Assign roles to users and remove them.",
)

# --- Audit ---------------------------------------------------------------
AUDIT_READ: Final = PermissionSpec(
    "audit.event.read",
    "Read the tenant's audit records.",
)

#: Every permission the application enforces.
ALL_PERMISSIONS: Final[tuple[PermissionSpec, ...]] = (
    USER_READ,
    USER_WRITE,
    ROLE_READ,
    ROLE_WRITE,
    ROLE_ASSIGN,
    AUDIT_READ,
)

#: Provisional baseline role. Exists so a tenant is administrable at all before
#: the authority matrix is defined; see A-04 in the assumptions register. It is
#: expected to be replaced, not extended.
TENANT_ADMIN_ROLE: Final = "tenant_admin"

TENANT_ADMIN_PERMISSIONS: Final[tuple[PermissionSpec, ...]] = ALL_PERMISSIONS

PROVISIONAL_ROLE_DESCRIPTION: Final = (
    "PROVISIONAL baseline role. Grants identity administration so the tenant "
    "can be managed before the business authority matrix is defined. Not a "
    "business decision -- replace when requirements are available (A-04)."
)


def permission_codes() -> frozenset[str]:
    """Every enforced permission code."""
    return frozenset(spec.code for spec in ALL_PERMISSIONS)
