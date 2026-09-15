"""Attribute resolution and operator tests.

These cover the two extension points that let A-13, A-14, geography and
organisation arrive later without touching the evaluator: registering an
attribute resolver, and registering an operator.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.policy.attributes import (
    AttributeRegistry,
    DuplicateNamespaceError,
    RequestAttributeResolver,
    SubjectAttributeResolver,
    access_request,
    default_registry,
)
from app.policy.context import AccessRequest, AttributeBag, AttributeValue
from app.policy.operators import (
    Operator,
    OperatorSpec,
    get_operator,
    register_operator,
    registered_operators,
)

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
SUBJECT = uuid.UUID("11111111-0000-4000-8000-000000000001")


def _request(**kwargs) -> AccessRequest:
    return access_request(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        action="read",
        resource_type="asset",
        **kwargs,
    )


class DepartmentResolver:
    """A resolver of the kind organisation or geography will need."""

    namespace = "org"

    def __init__(self, department: str = "finance") -> None:
        self._department = department

    async def resolve(
        self,
        request: AccessRequest,
        session: AsyncSession,
    ) -> Mapping[str, AttributeValue]:
        return {"department": self._department, "regions": frozenset({"emea", "apac"})}


class BadlyBehavedResolver:
    namespace = "bad"

    async def resolve(
        self,
        request: AccessRequest,
        session: AsyncSession,
    ) -> Mapping[str, AttributeValue]:
        return {"subject.id": "forged"}


# --- AttributeBag --------------------------------------------------------


def test_bag_distinguishes_absent_from_none() -> None:
    bag = AttributeBag({"a": None})

    assert bag.has("a") is True
    assert bag.get("a") is None
    assert bag.has("b") is False


def test_bag_merge_layers_on_top() -> None:
    merged = AttributeBag({"a": "1", "b": "2"}).merged_with({"b": "3"})

    assert merged.get("a") == "1"
    assert merged.get("b") == "3"


# --- Built-in resolvers --------------------------------------------------


async def test_request_resolver_describes_the_request() -> None:
    registry = AttributeRegistry([RequestAttributeResolver()])

    bag = await registry.resolve(_request(), session=None)  # type: ignore[arg-type]

    assert bag.get("request.action") == "read"
    assert bag.get("request.resource_type") == "asset"


async def test_subject_resolver_describes_the_caller() -> None:
    registry = AttributeRegistry([SubjectAttributeResolver()])

    bag = await registry.resolve(_request(), session=None)  # type: ignore[arg-type]

    assert bag.get("subject.id") == str(SUBJECT)
    assert bag.get("subject.tenant_id") == str(TENANT)


async def test_default_registry_carries_structure_and_jurisdiction() -> None:
    """Jurisdiction is specified (Master Prompt §10) so it ships; the still
    unspecified domains do not.

    This assertion previously pinned the *absence* of geography. P02 resolved
    A-19, so geography joins -- while classification (A-13), audience (A-14)
    and organisation (A-20) remain deliberately absent.
    """
    assert default_registry().namespaces == frozenset(
        {"request", "subject", "geography"}
    )


async def test_default_registry_still_omits_the_unspecified_domains() -> None:
    """A-13, A-14 and A-20 are not yet defined; guessing them would be a
    fabrication under Master Prompt §35."""
    namespaces = default_registry().namespaces

    assert "classification" not in namespaces
    assert "audience" not in namespaces
    assert "organisation" not in namespaces


async def test_resource_id_is_omitted_when_absent() -> None:
    registry = AttributeRegistry([RequestAttributeResolver()])

    bag = await registry.resolve(_request(), session=None)  # type: ignore[arg-type]

    assert bag.has("request.resource_id") is False


# --- Extensibility -------------------------------------------------------


async def test_a_resolver_can_be_registered_and_contributes_attributes() -> None:
    """Built from the structural resolvers rather than ``default_registry()``:
    geography reads the tenant registry, and this test has no database."""
    registry = AttributeRegistry(
        [RequestAttributeResolver(), SubjectAttributeResolver()]
    )
    registry.register(DepartmentResolver())

    bag = await registry.resolve(_request(), session=None)  # type: ignore[arg-type]

    assert bag.get("org.department") == "finance"
    assert bag.get("org.regions") == frozenset({"emea", "apac"})


def test_duplicate_namespaces_are_refused() -> None:
    """Two resolvers must never be able to contest one attribute."""
    registry = default_registry()

    with pytest.raises(DuplicateNamespaceError):
        registry.register(RequestAttributeResolver())


def test_a_namespace_can_be_replaced_deliberately() -> None:
    registry = default_registry()
    registry.register(RequestAttributeResolver(), replace=True)

    assert "request" in registry.namespaces


async def test_a_resolver_cannot_forge_another_namespace() -> None:
    registry = AttributeRegistry([BadlyBehavedResolver()])

    with pytest.raises(ValueError, match="qualified key"):
        await registry.resolve(_request(), session=None)  # type: ignore[arg-type]


def test_namespace_must_not_contain_the_separator() -> None:
    class Bad:
        namespace = "a.b"

        async def resolve(self, request, session):  # type: ignore[no-untyped-def]
            return {}

    with pytest.raises(ValueError, match="must not contain"):
        AttributeRegistry([Bad()])


# --- Operators -----------------------------------------------------------


def test_default_operators_are_registered() -> None:
    assert Operator.EQUALS in registered_operators()
    assert Operator.CONTAINS_ANY in registered_operators()


def test_no_ordering_operator_ships_by_default() -> None:
    """A-13: shipping one would presume classification levels are ordered."""
    for name in ("greater_than", "less_than", "dominates", "at_least"):
        assert get_operator(name) is None


def test_set_operators_make_no_assumption_about_membership_semantics() -> None:
    """A-14: subset and intersection are both available; neither is privileged."""
    contains_all = get_operator(Operator.CONTAINS_ALL)
    contains_any = get_operator(Operator.CONTAINS_ANY)
    assert contains_all is not None and contains_any is not None

    value = frozenset({"partners", "internal"})

    assert contains_all.evaluate(value, True, ["partners", "internal"]) is True
    assert contains_all.evaluate(value, True, ["partners", "public"]) is False
    assert contains_any.evaluate(value, True, ["partners", "public"]) is True


def test_booleans_compare_as_lowercase_text() -> None:
    equals = get_operator(Operator.EQUALS)
    assert equals is not None

    assert equals.evaluate(True, True, ["true"]) is True
    assert equals.evaluate(False, True, ["false"]) is True


def test_an_ordering_operator_can_be_added_later() -> None:
    """The A-13 extension point, exercised without deciding A-13."""

    def numeric_at_least(value, resolved, operands) -> bool:  # type: ignore[no-untyped-def]
        if not resolved or value is None or not operands:
            return False
        return float(value) >= float(operands[0])

    register_operator(
        OperatorSpec("test_numeric_at_least", numeric_at_least, requires_operands=True),
        replace=True,
    )

    spec = get_operator("test_numeric_at_least")
    assert spec is not None
    assert spec.evaluate(5, True, ["3"]) is True
    assert spec.evaluate(2, True, ["3"]) is False


def test_registering_a_duplicate_operator_is_refused() -> None:
    spec = get_operator(Operator.EQUALS)
    assert spec is not None

    with pytest.raises(ValueError, match="already registered"):
        register_operator(spec)
