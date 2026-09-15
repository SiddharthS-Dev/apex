"""Tenant registry and policy decision point, against PostgreSQL 16.

The isolation tests here are the ones that matter: a policy belonging to one
tenant must never influence another tenant's decision, and it must not be
possible to reach one by asking nicely.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantContextMissingError, system_scope, tenant_scope
from app.policy import service
from app.policy.attributes import access_request, default_registry
from app.policy.engine import Effect, Outcome
from app.policy.models import Policy, PolicyCondition, Tenant
from app.policy.operators import Operator
from app.policy.service import (
    DuplicateTenantError,
    InvalidPolicyError,
    TenantNotFoundError,
)
from tests.test_policy_attributes import DepartmentResolver

SUBJECT = uuid.UUID("11111111-0000-4000-8000-000000000001")


def _request(tenant_id: uuid.UUID, action: str = "read", resource_type: str = "asset"):
    return access_request(
        tenant_id=tenant_id,
        subject_id=SUBJECT,
        action=action,
        resource_type=resource_type,
    )


# --- Tenant registry -----------------------------------------------------


async def test_tenant_can_be_registered(session: AsyncSession) -> None:
    tenant = await service.create_tenant(session, slug="acme", name="Acme Ltd")

    assert tenant.slug == "acme"
    assert tenant.is_active is True


async def test_tenant_slug_is_normalised(session: AsyncSession) -> None:
    tenant = await service.create_tenant(session, slug="  ACME  ", name="Acme")

    assert tenant.slug == "acme"


async def test_duplicate_tenant_slug_is_refused(session: AsyncSession) -> None:
    await service.create_tenant(session, slug="acme", name="Acme")

    with pytest.raises(DuplicateTenantError):
        await service.create_tenant(session, slug="acme", name="Acme Again")


async def test_empty_tenant_slug_is_refused(session: AsyncSession) -> None:
    with pytest.raises(InvalidPolicyError):
        await service.create_tenant(session, slug="   ", name="Nameless")


async def test_tenant_can_be_looked_up_by_id_and_slug(session: AsyncSession) -> None:
    created = await service.create_tenant(session, slug="acme", name="Acme")

    assert (await service.get_tenant(session, created.id)) is not None
    assert (await service.get_tenant_by_slug(session, "ACME")) is not None


async def test_listing_tenants_is_a_platform_operation(session: AsyncSession) -> None:
    await service.create_tenant(session, slug="acme", name="Acme")
    await service.create_tenant(session, slug="globex", name="Globex")

    assert [t.slug for t in await service.list_tenants(session)] == ["acme", "globex"]


async def test_inactive_tenant_is_not_usable(session: AsyncSession) -> None:
    tenant = await service.create_tenant(session, slug="acme", name="Acme")
    with system_scope():
        tenant.is_active = False
        await session.commit()

    with pytest.raises(TenantNotFoundError):
        await service.require_active_tenant(session, tenant.id)


async def test_unknown_tenant_is_refused(session: AsyncSession) -> None:
    with pytest.raises(TenantNotFoundError):
        await service.require_active_tenant(session, uuid.uuid4())


async def test_tenant_registry_is_global_not_tenant_scoped(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """The registry defines tenants, so it cannot be filtered by one."""
    await service.create_tenant(session, slug="acme", name="Acme", tenant_id=tenant_a)

    with tenant_scope(uuid.uuid4()):
        found = (await session.execute(select(Tenant))).scalars().all()

    assert [t.slug for t in found] == ["acme"]


# --- Policy definition ---------------------------------------------------


async def test_policy_is_created_within_its_tenant(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    policy = await service.create_policy(
        session, tenant_id=tenant_a, name="allow-read", effect=Effect.ALLOW
    )

    assert policy.tenant_id == tenant_a
    assert policy.effect == "allow"


async def test_policy_conditions_are_stored(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_policy(
        session,
        tenant_id=tenant_a,
        name="allow-finance",
        effect=Effect.ALLOW,
        conditions=[("org.department", Operator.EQUALS, ["finance"])],
    )

    with tenant_scope(tenant_a):
        rows = (await session.execute(select(PolicyCondition))).scalars().all()

    assert [(r.attribute_key, r.operator, r.operands) for r in rows] == [
        ("org.department", "equals", ["finance"])
    ]


async def test_unknown_operator_is_refused_at_write_time(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """A typo should fail loudly here, not silently never match later."""
    with pytest.raises(InvalidPolicyError, match="Unknown operator"):
        await service.create_policy(
            session,
            tenant_id=tenant_a,
            name="broken",
            effect=Effect.ALLOW,
            conditions=[("org.department", "dominates", ["finance"])],
        )


async def test_operator_missing_operands_is_refused_at_write_time(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    with pytest.raises(InvalidPolicyError, match="requires operands"):
        await service.create_policy(
            session,
            tenant_id=tenant_a,
            name="broken",
            effect=Effect.ALLOW,
            conditions=[("org.department", Operator.EQUALS, [])],
        )


async def test_unknown_effect_is_refused(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    with pytest.raises(InvalidPolicyError, match="Unknown effect"):
        await service.create_policy(
            session, tenant_id=tenant_a, name="odd", effect="maybe"
        )


# --- Decisions -----------------------------------------------------------


async def test_decision_allows_when_a_policy_matches(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_policy(
        session,
        tenant_id=tenant_a,
        name="allow-read",
        effect=Effect.ALLOW,
        action="read",
        resource_type="asset",
    )

    decision = await service.decide(session, _request(tenant_a))

    assert decision.outcome is Outcome.ALLOW


async def test_decision_is_not_applicable_with_no_policies(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    decision = await service.decide(session, _request(tenant_a))

    assert decision.outcome is Outcome.NOT_APPLICABLE
    assert decision.allowed is False


async def test_deny_overrides_allow_end_to_end(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_policy(
        session, tenant_id=tenant_a, name="allow-all", effect=Effect.ALLOW
    )
    await service.create_policy(
        session, tenant_id=tenant_a, name="deny-read", effect=Effect.DENY, action="read"
    )

    decision = await service.decide(session, _request(tenant_a))

    assert decision.outcome is Outcome.DENY


async def test_inactive_policies_are_not_loaded(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    policy = await service.create_policy(
        session, tenant_id=tenant_a, name="allow-all", effect=Effect.ALLOW
    )
    with tenant_scope(tenant_a):
        policy.is_active = False
        await session.commit()

    assert (await service.decide(session, _request(tenant_a))).allowed is False


async def test_decision_uses_resolved_attributes(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    await service.create_policy(
        session,
        tenant_id=tenant_a,
        name="allow-subject",
        effect=Effect.ALLOW,
        conditions=[("subject.id", Operator.EQUALS, [str(SUBJECT)])],
    )

    assert (await service.decide(session, _request(tenant_a))).allowed is True


async def test_a_custom_resolver_feeds_the_decision(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """Extensibility end to end: a new attribute drives a real decision."""
    await service.create_policy(
        session,
        tenant_id=tenant_a,
        name="allow-finance",
        effect=Effect.ALLOW,
        conditions=[("org.department", Operator.EQUALS, ["finance"])],
    )

    registry = default_registry()
    registry.register(DepartmentResolver("finance"))
    allowed = await service.decide(session, _request(tenant_a), registry=registry)

    registry_other = default_registry()
    registry_other.register(DepartmentResolver("legal"))
    refused = await service.decide(session, _request(tenant_a), registry=registry_other)

    assert allowed.allowed is True
    assert refused.allowed is False


async def test_policy_referencing_an_unresolved_attribute_does_not_apply(
    session: AsyncSession,
    tenant_a: uuid.UUID,
) -> None:
    """No resolver provides org.department, so the rule cannot grant."""
    await service.create_policy(
        session,
        tenant_id=tenant_a,
        name="allow-finance",
        effect=Effect.ALLOW,
        conditions=[("org.department", Operator.EQUALS, ["finance"])],
    )

    assert (await service.decide(session, _request(tenant_a))).allowed is False


# --- Tenant isolation ----------------------------------------------------


async def test_policies_are_invisible_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await service.create_policy(
        session, tenant_id=tenant_b, name="allow-all", effect=Effect.ALLOW
    )

    with tenant_scope(tenant_a):
        found = (await session.execute(select(Policy))).scalars().all()

    assert found == []


async def test_another_tenants_allow_does_not_grant(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """The decisive isolation test for the PDP."""
    await service.create_policy(
        session, tenant_id=tenant_b, name="allow-all", effect=Effect.ALLOW
    )

    decision = await service.decide(session, _request(tenant_a))

    assert decision.outcome is Outcome.NOT_APPLICABLE


async def test_another_tenants_deny_does_not_refuse(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Isolation cuts both ways: B's deny must not affect A."""
    await service.create_policy(
        session, tenant_id=tenant_a, name="allow-all", effect=Effect.ALLOW
    )
    await service.create_policy(
        session, tenant_id=tenant_b, name="deny-all", effect=Effect.DENY
    )

    assert (await service.decide(session, _request(tenant_a))).outcome is Outcome.ALLOW


async def test_loading_policies_without_a_tenant_is_refused(
    session: AsyncSession,
) -> None:
    with pytest.raises(TenantContextMissingError):
        await session.execute(select(Policy))


async def test_policy_conditions_do_not_leak_across_tenants(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """Regression for the Commit 003/004 guard gap: a COLUMN select across a
    join of tenant-scoped tables must still be filtered."""
    await service.create_policy(
        session,
        tenant_id=tenant_b,
        name="allow-finance",
        effect=Effect.ALLOW,
        conditions=[("org.department", Operator.EQUALS, ["finance"])],
    )

    with tenant_scope(tenant_a):
        keys = (
            await session.execute(
                select(PolicyCondition.attribute_key).join(
                    Policy, Policy.id == PolicyCondition.policy_id
                )
            )
        ).scalars().all()

    assert keys == []


async def test_system_scope_sees_every_tenants_policies(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    await service.create_policy(
        session, tenant_id=tenant_a, name="a", effect=Effect.ALLOW
    )
    await service.create_policy(
        session, tenant_id=tenant_b, name="b", effect=Effect.ALLOW
    )

    with system_scope():
        names = {p.name for p in (await session.execute(select(Policy))).scalars().all()}

    assert names == {"a", "b"}


async def test_decisions_remain_isolated_after_a_system_scope_block(
    session: AsyncSession,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """The escape hatch must not leave isolation weakened behind it."""
    await service.create_policy(
        session, tenant_id=tenant_b, name="allow-all", effect=Effect.ALLOW
    )

    with system_scope():
        pass

    assert (await service.decide(session, _request(tenant_a))).allowed is False
