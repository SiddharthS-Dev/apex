"""Core types shared by the three policy stages.

These live in their own module so that attribute resolution and policy decision
can both depend on them without depending on each other. That separation is the
point: the evaluator must be a pure function of *values*, never of where those
values came from.

``AttributeValue`` is deliberately narrow. Multi-valued attributes are a
``frozenset[str]`` -- which supports membership and subset questions without
asserting anything about whether the members nest, overlap or exclude one
another. Those are exactly the semantics that A-13 (classification) and A-14
(audience) have not defined, so nothing here presumes them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

#: A single resolved attribute value.
#:
#: Ordering is intentionally *not* modelled. An ordered classification scheme
#: would be expressed by registering an ordering-aware operator, not by giving
#: values a comparable type here -- see :mod:`app.policy.operators`.
AttributeValue: TypeAlias = str | int | float | bool | frozenset[str] | None


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """What is being asked, by whom, in which tenant.

    This is the input to attribute resolution and, through it, to the decision.
    It carries identifiers only -- never resolved attributes -- so that the two
    stages cannot be accidentally merged.
    """

    tenant_id: uuid.UUID
    subject_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: uuid.UUID | None = None

    #: Caller-supplied values a resolver may use. Not trusted as attributes in
    #: their own right: a resolver decides what, if anything, to derive.
    hints: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AttributeBag:
    """Resolved attributes, keyed ``namespace.name``.

    Immutable, so a decision cannot mutate the facts it was given.
    """

    values: Mapping[str, AttributeValue] = field(default_factory=dict)

    def get(self, key: str) -> AttributeValue:
        """Return an attribute, or ``None`` when it was not resolved."""
        return self.values.get(key)

    def has(self, key: str) -> bool:
        """Whether the attribute was resolved at all.

        Distinct from resolving to ``None``: "not provided" and "provided as
        empty" are different facts, and a policy may care which.
        """
        return key in self.values

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.values

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def merged_with(self, other: Mapping[str, AttributeValue]) -> AttributeBag:
        """Return a new bag with ``other`` layered on top."""
        return AttributeBag({**self.values, **other})
