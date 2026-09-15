"""Policy decision -- stage three of the pipeline.

    Policy Definition -> Attribute Resolution -> **Policy Decision** -> Enforcement

The evaluator is a **pure function** of three inputs: the request, the resolved
attributes, and the policies. It performs no I/O, touches no session, and knows
nothing about tenants beyond the value it was handed. That is what makes it
deterministic and exhaustively testable -- and it is also why tenant isolation
cannot be bypassed *here*: the engine never loads anything, so what it sees is
entirely determined by the caller, which does load under tenant scope.

**Missing attributes never satisfy a condition.** An unresolved attribute makes
its condition false, including for negative operators: "not equal to X" is not
true of something we do not know. The alternative -- treating absence as
satisfying a negation -- turns a resolver outage into a silent grant.

**Conflict resolution is deny-overrides**, and that is a *safety* default
rather than a business rule. No other combining algorithm is implemented,
because choosing between permit-overrides, first-applicable or an explicit
priority order is a decision the requirements have not made (A-04). The
combiner is a registered function, so adding one later changes no other code.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.policy.context import AccessRequest, AttributeBag
from app.policy.models import Policy
from app.policy.operators import get_operator

#: Matches any action or resource type.
WILDCARD = "*"


class Effect(StrEnum):
    """What a policy does when it applies."""

    ALLOW = "allow"
    DENY = "deny"


class Outcome(StrEnum):
    """The result of evaluating a request."""

    ALLOW = "allow"
    DENY = "deny"
    #: No policy spoke to this request. Enforcement treats this as a refusal;
    #: it is kept distinct so "refused by rule" and "no rule" are
    #: distinguishable in an audit trail.
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class PolicyTrace:
    """Why one policy did or did not apply."""

    policy_id: str
    policy_name: str
    effect: str
    applied: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome, with enough detail to explain itself."""

    outcome: Outcome
    request: AccessRequest
    traces: tuple[PolicyTrace, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        """Whether the request may proceed. Anything but ALLOW is a refusal."""
        return self.outcome is Outcome.ALLOW

    @property
    def deciding_policies(self) -> tuple[str, ...]:
        """Ids of the policies that actually applied."""
        return tuple(trace.policy_id for trace in self.traces if trace.applied)


def _targets(policy: Policy, request: AccessRequest) -> bool:
    """Whether the policy speaks to this action and resource type."""
    action_ok = policy.action in (WILDCARD, request.action)
    resource_ok = policy.resource_type in (WILDCARD, request.resource_type)
    return action_ok and resource_ok


def _conditions_hold(policy: Policy, attributes: AttributeBag) -> tuple[bool, str]:
    """Whether every condition on the policy is satisfied."""
    for condition in policy.conditions:
        spec = get_operator(condition.operator)
        if spec is None:
            # An unknown operator makes the policy inapplicable rather than
            # raising: a single malformed row must not break every decision,
            # and failing closed on one policy is the safe direction.
            return False, f"unknown operator {condition.operator!r}"

        operands = list(condition.operands or ())
        if spec.requires_operands and not operands:
            return False, f"operator {condition.operator!r} needs operands"

        value = attributes.get(condition.attribute_key)
        resolved = attributes.has(condition.attribute_key)

        if not spec.evaluate(value, resolved, operands):
            detail = "unresolved" if not resolved else "did not match"
            return False, f"{condition.attribute_key} {detail}"

    return True, "all conditions held"


def evaluate_policy(
    policy: Policy,
    request: AccessRequest,
    attributes: AttributeBag,
) -> PolicyTrace:
    """Decide whether one policy applies, and record why."""
    identifier = str(policy.id)

    if not policy.is_active:
        return PolicyTrace(identifier, policy.name, policy.effect, False, "inactive")

    if not _targets(policy, request):
        return PolicyTrace(
            identifier, policy.name, policy.effect, False, "action or resource mismatch"
        )

    holds, reason = _conditions_hold(policy, attributes)
    return PolicyTrace(identifier, policy.name, policy.effect, holds, reason)


CombiningAlgorithm = Callable[[Sequence[PolicyTrace]], Outcome]


def deny_overrides(traces: Sequence[PolicyTrace]) -> Outcome:
    """Any applicable deny wins; otherwise any applicable allow; else nothing.

    A safety default, not a business precedence rule. It is the only algorithm
    implemented, because any other ordering encodes an authority decision the
    requirements have not made.
    """
    applied = [trace for trace in traces if trace.applied]
    if not applied:
        return Outcome.NOT_APPLICABLE
    if any(trace.effect == Effect.DENY for trace in applied):
        return Outcome.DENY
    if any(trace.effect == Effect.ALLOW for trace in applied):
        return Outcome.ALLOW
    # An unrecognised effect value is treated as a refusal rather than ignored.
    return Outcome.DENY


_ALGORITHMS: dict[str, CombiningAlgorithm] = {"deny_overrides": deny_overrides}


def register_combining_algorithm(
    name: str,
    algorithm: CombiningAlgorithm,
    *,
    replace: bool = False,
) -> None:
    """Add a combining algorithm.

    The extension point for A-04: once the requirements define precedence, it
    is registered here rather than written into :func:`evaluate`.
    """
    if not replace and name in _ALGORITHMS:
        raise ValueError(f"Combining algorithm {name!r} is already registered")
    _ALGORITHMS[name] = algorithm


def get_combining_algorithm(name: str) -> CombiningAlgorithm | None:
    return _ALGORITHMS.get(name)


def registered_combining_algorithms() -> frozenset[str]:
    return frozenset(_ALGORITHMS)


def evaluate(
    request: AccessRequest,
    attributes: AttributeBag,
    policies: Sequence[Policy],
    *,
    algorithm: str = "deny_overrides",
) -> Decision:
    """Decide a request against a set of policies.

    Pure: no I/O, no session, no globals beyond the operator and algorithm
    registries. The caller is responsible for having loaded ``policies`` under
    the correct tenant scope -- see :mod:`app.policy.service`.
    """
    combiner = get_combining_algorithm(algorithm)
    if combiner is None:
        raise ValueError(f"Unknown combining algorithm {algorithm!r}")

    traces = tuple(evaluate_policy(policy, request, attributes) for policy in policies)
    return Decision(outcome=combiner(traces), request=request, traces=traces)
