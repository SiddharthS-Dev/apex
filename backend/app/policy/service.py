"""Tenant registry operations and the policy decision point.

This module is where the pure evaluator meets the database, and therefore where
tenant isolation is enforced for policy data:

- Policies are **always** loaded inside ``tenant_scope(request.tenant_id)``, so
  the session guards filter them. A caller cannot pass in a tenant and receive
  another tenant's rules.
- The tenant registry is global, so reading it across tenants is a genuine
  platform operation and uses ``system_scope()`` explicitly, at commented call
  sites.

The evaluator itself never loads anything, so there is no path by which a
decision sees a policy the tenant scope would have hidden.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import system_scope, tenant_scope
from app.policy.attributes import AttributeRegistry, default_registry
from app.policy.context import AccessRequest, AttributeBag
from app.policy.engine import Decision, Effect, evaluate
from app.policy.models import Policy, PolicyCondition, Tenant
from app.policy.operators import get_operator


class PolicyError(Exception):
    """Base class for policy-layer failures."""


class TenantNotFoundError(PolicyError):
    """No tenant with that identifier or slug."""


class DuplicateTenantError(PolicyError):
    """A tenant with that slug already exists."""


class InvalidPolicyError(PolicyError):
    """A policy definition could not be accepted."""


# --- Tenant registry -----------------------------------------------------


async def create_tenant(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    tenant_id: uuid.UUID | None = None,
) -> Tenant:
    """Register a tenant.

    Platform-level: creating a tenant is by definition not done from inside
    one, so this runs in ``system_scope()``.
    """
    normalised = slug.strip().lower()
    if not normalised:
        raise InvalidPolicyError("A tenant slug must not be empty")

    with system_scope():
        existing = (
            await session.execute(select(Tenant).where(Tenant.slug == normalised))
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateTenantError(f"Tenant {normalised!r} already exists")

        tenant = Tenant(slug=normalised, name=name)
        if tenant_id is not None:
            tenant.id = tenant_id
        session.add(tenant)
        await session.commit()
        return tenant


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    """Look up a tenant by id."""
    # The registry is what defines tenants, so it cannot be filtered by one.
    with system_scope():
        return (
            await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Tenant | None:
    """Look up a tenant by slug."""
    with system_scope():
        return (
            await session.execute(select(Tenant).where(Tenant.slug == slug.strip().lower()))
        ).scalar_one_or_none()


async def list_tenants(session: AsyncSession) -> Sequence[Tenant]:
    """Every registered tenant. Platform-level by nature."""
    with system_scope():
        return (await session.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()


async def require_active_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    """Fetch a tenant, raising unless it exists and is active."""
    tenant = await get_tenant(session, tenant_id)
    if tenant is None or not tenant.is_active:
        raise TenantNotFoundError(f"No active tenant {tenant_id}")
    return tenant


# --- Policy definition ---------------------------------------------------


async def create_policy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    effect: Effect | str,
    action: str = "*",
    resource_type: str = "*",
    description: str = "",
    conditions: Sequence[tuple[str, str, Sequence[str]]] = (),
) -> Policy:
    """Define a policy within a tenant.

    ``conditions`` are ``(attribute_key, operator, operands)`` triples. The
    operator is validated against the registry at write time so a typo fails
    loudly here rather than silently making the policy inapplicable at
    decision time.
    """
    effect_value = Effect(effect).value if not isinstance(effect, str) else effect
    if effect_value not in {Effect.ALLOW, Effect.DENY}:
        raise InvalidPolicyError(f"Unknown effect {effect_value!r}")

    for attribute_key, operator, operands in conditions:
        spec = get_operator(operator)
        if spec is None:
            raise InvalidPolicyError(f"Unknown operator {operator!r}")
        if spec.requires_operands and not list(operands):
            raise InvalidPolicyError(f"Operator {operator!r} requires operands")
        if not attribute_key:
            raise InvalidPolicyError("A condition needs an attribute key")

    with tenant_scope(tenant_id):
        policy = Policy(
            name=name,
            description=description,
            effect=effect_value,
            action=action,
            resource_type=resource_type,
        )
        session.add(policy)
        await session.flush()

        for attribute_key, operator, operands in conditions:
            session.add(
                PolicyCondition(
                    policy_id=policy.id,
                    attribute_key=attribute_key,
                    operator=operator,
                    operands=list(operands),
                )
            )

        await session.commit()
        return policy


async def load_policies(session: AsyncSession, tenant_id: uuid.UUID) -> Sequence[Policy]:
    """Every active policy belonging to one tenant.

    Loaded under ``tenant_scope``, so the session guards do the filtering --
    there is no hand-written tenant predicate to get wrong, and a tenant cannot
    be handed another's rules.
    """
    with tenant_scope(tenant_id):
        return (
            (await session.execute(select(Policy).where(Policy.is_active.is_(True))))
            .scalars()
            .all()
        )


# --- Policy decision point ----------------------------------------------


async def resolve_attributes(
    session: AsyncSession,
    request: AccessRequest,
    registry: AttributeRegistry | None = None,
) -> AttributeBag:
    """Run attribute resolution for a request."""
    registry = registry or default_registry()
    with tenant_scope(request.tenant_id):
        return await registry.resolve(request, session)


async def decide(
    session: AsyncSession,
    request: AccessRequest,
    *,
    registry: AttributeRegistry | None = None,
    algorithm: str = "deny_overrides",
) -> Decision:
    """Resolve attributes, load this tenant's policies, and evaluate.

    The whole decision path is bound to ``request.tenant_id``. Evaluating
    against another tenant's policies is not expressible here: it would require
    loading them, and loading is tenant-scoped.
    """
    attributes = await resolve_attributes(session, request, registry)
    policies = await load_policies(session, request.tenant_id)
    return evaluate(request, attributes, policies, algorithm=algorithm)
