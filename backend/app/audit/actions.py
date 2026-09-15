"""The audit vocabulary: actor kinds, outcomes, and action names.

**The registry is a catalogue, not a gate.** Recording an unregistered action is
permitted, deliberately. An audit system that refuses events it does not
recognise loses exactly the events most worth having -- the novel ones, and the
ones from a context that shipped after the vocabulary was written. The registry
exists so callers have constants to use and reviewers have a list to read, not
so the writer can reject.

**Technical versus business.** Every action below is ``TECHNICAL``: it names a
mechanical event in a context that already exists. No business event taxonomy is
defined here. Gate transitions, approvals, publication and retention events
belong to Commit 012 and need the authority requirements (A-04) to name
correctly; inventing them now would put unapproved vocabulary into an immutable
record.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, NamedTuple


class ActorType(StrEnum):
    """What kind of principal caused an event."""

    #: An authenticated person.
    USER = "user"
    #: The platform itself -- migrations, scheduled work, startup tasks. These
    #: are the events performed under system_scope(), and they are marked so
    #: that privileged activity is never indistinguishable from a user's.
    SYSTEM = "system"
    #: An unauthenticated caller. A failed login has no user to attribute to,
    #: but the attempt is still worth recording.
    ANONYMOUS = "anonymous"


class Outcome(StrEnum):
    """How the audited operation ended."""

    SUCCESS = "success"
    FAILURE = "failure"
    #: Authenticated but refused -- by RBAC, by policy, or by tenant isolation.
    #: Distinct from FAILURE so "attempted and blocked" is greppable.
    DENIED = "denied"


class ActionOrigin(StrEnum):
    """Where an action name came from."""

    #: Names a mechanical event in existing code.
    TECHNICAL = "technical"
    #: Approved by the business requirements. None yet -- see the module note.
    BUSINESS = "business"


class ActionSpec(NamedTuple):
    """A catalogued action."""

    name: str
    description: str
    origin: str = ActionOrigin.TECHNICAL


# --- Identity ------------------------------------------------------------
USER_CREATED: Final = ActionSpec("identity.user.created", "A user was created.")
USER_AUTHENTICATED: Final = ActionSpec(
    "identity.user.authenticated", "A user authenticated successfully."
)
USER_AUTHENTICATION_FAILED: Final = ActionSpec(
    "identity.user.authentication_failed", "An authentication attempt was rejected."
)
SESSION_CREATED: Final = ActionSpec("identity.session.created", "A session was issued.")
SESSION_REFRESHED: Final = ActionSpec(
    "identity.session.refreshed", "A refresh token was exchanged and rotated."
)
SESSION_REVOKED: Final = ActionSpec("identity.session.revoked", "A session was revoked.")
ROLE_ASSIGNED: Final = ActionSpec("identity.role.assigned", "A role was assigned to a user.")
ROLE_REVOKED: Final = ActionSpec("identity.role.revoked", "A role was removed from a user.")
PERMISSION_DENIED: Final = ActionSpec(
    "identity.permission.denied", "An authenticated caller lacked a required permission."
)

# --- Policy --------------------------------------------------------------
TENANT_CREATED: Final = ActionSpec("policy.tenant.created", "A tenant was registered.")
POLICY_CREATED: Final = ActionSpec("policy.policy.created", "A policy was defined.")
ACCESS_DENIED: Final = ActionSpec(
    "policy.access.denied", "A policy decision refused a request."
)

# --- Object storage ------------------------------------------------------
OBJECT_STORED: Final = ActionSpec(
    "storage.object.stored", "Bytes were written to object storage."
)
OBJECT_DELETED: Final = ActionSpec(
    "storage.object.deleted", "An object's bytes were removed from storage."
)
OBJECT_SIGNED_URL_ISSUED: Final = ActionSpec(
    "storage.object.signed_url_issued",
    "A short-lived read URL was issued for an object.",
)
OBJECT_ACCESS_DENIED: Final = ActionSpec(
    "storage.object.access_denied",
    "An object was requested that this tenant cannot reach.",
)
OBJECT_INTEGRITY_FAILED: Final = ActionSpec(
    "storage.object.integrity_check_failed",
    "Stored bytes did not match their recorded hash and were withheld.",
)

# --- Audit itself --------------------------------------------------------
AUDIT_READ: Final = ActionSpec("audit.event.read", "Audit records were queried.")


#: Everything catalogued. Not exhaustive of what may be recorded.
ALL_ACTIONS: Final[tuple[ActionSpec, ...]] = (
    USER_CREATED,
    USER_AUTHENTICATED,
    USER_AUTHENTICATION_FAILED,
    SESSION_CREATED,
    SESSION_REFRESHED,
    SESSION_REVOKED,
    ROLE_ASSIGNED,
    ROLE_REVOKED,
    PERMISSION_DENIED,
    TENANT_CREATED,
    POLICY_CREATED,
    ACCESS_DENIED,
    OBJECT_STORED,
    OBJECT_DELETED,
    OBJECT_SIGNED_URL_ISSUED,
    OBJECT_ACCESS_DENIED,
    OBJECT_INTEGRITY_FAILED,
    AUDIT_READ,
)

_BY_NAME: Final[dict[str, ActionSpec]] = {spec.name: spec for spec in ALL_ACTIONS}


def action_names() -> frozenset[str]:
    """Every catalogued action name."""
    return frozenset(_BY_NAME)


def get_action(name: str) -> ActionSpec | None:
    """Look up a catalogued action, or ``None`` if it is not catalogued."""
    return _BY_NAME.get(name)


def is_catalogued(name: str) -> bool:
    """Whether an action name appears in the catalogue.

    Informational. Recording an uncatalogued action is allowed.
    """
    return name in _BY_NAME


# --- Resource types ------------------------------------------------------
# Deliberately thin: only the resources that exist today. Ontology entities
# (A-02) are absent, and the audit writer accepts any string, so a future
# context records against its own resource types without changing this module.
class ResourceType(StrEnum):
    """Resource kinds that exist in the platform today."""

    USER = "identity.user"
    ROLE = "identity.role"
    SESSION = "identity.session"
    TENANT = "policy.tenant"
    POLICY = "policy.policy"
    AUDIT_EVENT = "audit.event"
    STORED_OBJECT = "storage.object"
