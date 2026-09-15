"""Tenant registry operations and the policy decision point.

This module is where the pure evaluator meets the database, and therefore where
tenant isolation is enforced for policy data. Policies are **always** loaded
inside ``tenant_scope(request.tenant_id)``, so the session guards filter them: a
caller cannot pass in a tenant and receive another tenant's rules.

The evaluator itself never loads anything, so there is no path by which a
decision sees a policy the tenant scope would have hidden.

Tenant registry operations moved to :mod:`app.platform.service` in P02.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_scope
from app.policy.attributes import AttributeRegistry, default_registry
from app.policy.context import AccessRequest, AttributeBag
from app.policy.engine import Decision, Effect, evaluate
from app.policy.models import Policy, PolicyCondition
from app.policy.operators import get_operator


class PolicyError(Exception):
    """Base class for policy-layer failures."""


class InvalidPolicyError(PolicyError):
    """A policy definition could not be accepted."""


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
