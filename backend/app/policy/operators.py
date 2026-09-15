"""Condition operators, as a registry.

Every operator here is **semantics-free**: it asks a question about values
without assuming what those values mean. That is what lets the four undefined
attribute domains be added later without touching the evaluator.

**What is deliberately absent.** There is no ordering operator -- no
``greater_than``, no ``dominates``, no lattice comparison. Providing one now
would encode an answer to A-13 (whether data classification levels are ordered)
that the requirements have not given. When they do, an ordering operator is
*registered* here; the engine, the schema and existing policies are untouched.

Similarly, nothing binds an attribute to an operator. Whether audience matching
is subset, intersection or equality (A-14) is a property of the policy someone
writes, not of this module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.policy.context import AttributeValue


class Operator(StrEnum):
    """The comparison a condition performs."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    #: The attribute's scalar value is one of the policy's literals.
    IN = "in"
    NOT_IN = "not_in"
    #: The attribute's set contains every one of the policy's literals.
    CONTAINS_ALL = "contains_all"
    #: The attribute's set contains at least one of the policy's literals.
    CONTAINS_ANY = "contains_any"
    #: The attribute was resolved at all, whatever its value.
    PRESENT = "present"
    ABSENT = "absent"


#: Signature of an operator: the resolved value, whether it was resolved at
#: all, and the policy's literal operands.
OperatorFn = Callable[[AttributeValue, bool, Sequence[str]], bool]


def _as_set(value: AttributeValue) -> frozenset[str]:
    """Coerce a resolved value to a set of strings for set comparisons."""
    if value is None:
        return frozenset()
    if isinstance(value, frozenset):
        return value
    return frozenset({_as_text(value)})


def _as_text(value: AttributeValue) -> str:
    """Render a scalar for comparison against a policy literal.

    Policy literals are stored as text, so booleans are normalised to
    ``"true"``/``"false"`` rather than Python's ``"True"``/``"False"``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _equals(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    if not resolved or value is None or not operands:
        return False
    if isinstance(value, frozenset):
        return value == frozenset(operands)
    return _as_text(value) == operands[0]


def _not_equals(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    # An unresolved attribute is not "not equal" -- it is unknown, and unknown
    # must not satisfy a condition. See the module note on missing attributes.
    if not resolved:
        return False
    return not _equals(value, resolved, operands)


def _in(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    if not resolved or value is None:
        return False
    return _as_text(value) in set(operands)


def _not_in(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    if not resolved:
        return False
    return not _in(value, resolved, operands)


def _contains_all(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    if not resolved or not operands:
        return False
    return set(operands).issubset(_as_set(value))


def _contains_any(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    if not resolved or not operands:
        return False
    return bool(set(operands) & _as_set(value))


def _present(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    return resolved


def _absent(value: AttributeValue, resolved: bool, operands: Sequence[str]) -> bool:
    return not resolved


@dataclass(frozen=True, slots=True)
class OperatorSpec:
    """An operator and how it behaves."""

    operator: str
    evaluate: OperatorFn
    #: Whether the operator needs at least one literal operand.
    requires_operands: bool


_REGISTRY: dict[str, OperatorSpec] = {}


def register_operator(spec: OperatorSpec, *, replace: bool = False) -> None:
    """Add an operator to the registry.

    This is the extension point for A-13: an ordering-aware comparison is
    registered here rather than built into the engine.
    """
    if not replace and spec.operator in _REGISTRY:
        raise ValueError(f"Operator {spec.operator!r} is already registered")
    _REGISTRY[spec.operator] = spec


def get_operator(name: str) -> OperatorSpec | None:
    """Look up an operator, or ``None`` if it is not registered."""
    return _REGISTRY.get(name)


def registered_operators() -> frozenset[str]:
    """Every registered operator name."""
    return frozenset(_REGISTRY)


def _install_defaults() -> None:
    defaults = (
        OperatorSpec(Operator.EQUALS, _equals, requires_operands=True),
        OperatorSpec(Operator.NOT_EQUALS, _not_equals, requires_operands=True),
        OperatorSpec(Operator.IN, _in, requires_operands=True),
        OperatorSpec(Operator.NOT_IN, _not_in, requires_operands=True),
        OperatorSpec(Operator.CONTAINS_ALL, _contains_all, requires_operands=True),
        OperatorSpec(Operator.CONTAINS_ANY, _contains_any, requires_operands=True),
        OperatorSpec(Operator.PRESENT, _present, requires_operands=False),
        OperatorSpec(Operator.ABSENT, _absent, requires_operands=False),
    )
    for spec in defaults:
        register_operator(spec, replace=True)


_install_defaults()
