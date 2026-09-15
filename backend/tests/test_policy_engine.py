"""Policy decision tests.

The evaluator is a pure function, so these need no database. That is the point
of the separation: the decision logic is exhaustively testable without a
session, a tenant, or a web framework.

Policies are built as lightweight stand-ins rather than ORM instances, so these
tests exercise the evaluator and nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from app.policy.attributes import access_request
from app.policy.context import AttributeBag
from app.policy.engine import (
    Effect,
    Outcome,
    deny_overrides,
    evaluate,
    get_combining_algorithm,
    register_combining_algorithm,
    registered_combining_algorithms,
)
from app.policy.operators import Operator

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
SUBJECT = uuid.UUID("11111111-0000-4000-8000-000000000001")


@dataclass
class FakeCondition:
    attribute_key: str
    operator: str
    operands: list[str] = field(default_factory=list)


@dataclass
class FakePolicy:
    name: str
    effect: str
    action: str = "*"
    resource_type: str = "*"
    is_active: bool = True
    conditions: list[FakeCondition] = field(default_factory=list)
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def _request(action: str = "read", resource_type: str = "asset"):
    return access_request(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        action=action,
        resource_type=resource_type,
    )


def _evaluate(policies, attributes=None, **kwargs):
    return evaluate(_request(), attributes or AttributeBag({}), policies, **kwargs)


# --- Default posture -----------------------------------------------------


def test_no_policies_is_not_applicable() -> None:
    """Silence is not permission, but it is distinguishable from a refusal."""
    decision = _evaluate([])

    assert decision.outcome is Outcome.NOT_APPLICABLE
    assert decision.allowed is False


def test_not_applicable_is_not_allowed() -> None:
    assert _evaluate([FakePolicy("other", Effect.ALLOW, action="write")]).allowed is False


# --- Allow and deny ------------------------------------------------------


def test_matching_allow_permits() -> None:
    decision = _evaluate([FakePolicy("p", Effect.ALLOW)])

    assert decision.outcome is Outcome.ALLOW
    assert decision.allowed is True


def test_matching_deny_refuses() -> None:
    assert _evaluate([FakePolicy("p", Effect.DENY)]).outcome is Outcome.DENY


def test_deny_overrides_allow() -> None:
    """The safety default when rules conflict."""
    decision = _evaluate(
        [FakePolicy("yes", Effect.ALLOW), FakePolicy("no", Effect.DENY)]
    )

    assert decision.outcome is Outcome.DENY


def test_inactive_policy_is_ignored() -> None:
    decision = _evaluate([FakePolicy("p", Effect.ALLOW, is_active=False)])

    assert decision.outcome is Outcome.NOT_APPLICABLE


def test_unrecognised_effect_refuses() -> None:
    """An effect the code does not understand must not read as permission."""
    assert _evaluate([FakePolicy("p", "maybe")]).outcome is Outcome.DENY


# --- Targeting -----------------------------------------------------------


def test_action_must_match() -> None:
    assert _evaluate([FakePolicy("p", Effect.ALLOW, action="write")]).allowed is False


def test_resource_type_must_match() -> None:
    assert (
        _evaluate([FakePolicy("p", Effect.ALLOW, resource_type="report")]).allowed is False
    )


def test_wildcards_match_anything() -> None:
    policy = FakePolicy("p", Effect.ALLOW, action="*", resource_type="*")

    assert _evaluate([policy]).allowed is True


def test_exact_action_and_resource_match() -> None:
    policy = FakePolicy("p", Effect.ALLOW, action="read", resource_type="asset")

    assert _evaluate([policy]).allowed is True


# --- Conditions ----------------------------------------------------------


def test_condition_on_a_resolved_attribute_can_match() -> None:
    policy = FakePolicy(
        "p",
        Effect.ALLOW,
        conditions=[FakeCondition("subject.id", Operator.EQUALS, [str(SUBJECT)])],
    )
    attributes = AttributeBag({"subject.id": str(SUBJECT)})

    assert _evaluate([policy], attributes).allowed is True


def test_every_condition_must_hold() -> None:
    policy = FakePolicy(
        "p",
        Effect.ALLOW,
        conditions=[
            FakeCondition("subject.id", Operator.EQUALS, [str(SUBJECT)]),
            FakeCondition("request.action", Operator.EQUALS, ["write"]),
        ],
    )
    attributes = AttributeBag({"subject.id": str(SUBJECT), "request.action": "read"})

    assert _evaluate([policy], attributes).allowed is False


# --- Missing and unknown attributes -------------------------------------


def test_missing_attribute_does_not_satisfy_a_condition() -> None:
    """A resolver outage must not become a silent grant."""
    policy = FakePolicy(
        "p",
        Effect.ALLOW,
        conditions=[FakeCondition("subject.department", Operator.EQUALS, ["finance"])],
    )

    assert _evaluate([policy], AttributeBag({})).allowed is False


def test_missing_attribute_does_not_satisfy_a_negative_condition() -> None:
    """"Not equal to X" is not true of something we do not know."""
    policy = FakePolicy(
        "p",
        Effect.ALLOW,
        conditions=[FakeCondition("subject.department", Operator.NOT_EQUALS, ["finance"])],
    )

    assert _evaluate([policy], AttributeBag({})).allowed is False


def test_missing_attribute_on_a_deny_also_fails_to_apply() -> None:
    """Applied consistently: absence makes a rule inapplicable either way."""
    policy = FakePolicy(
        "p",
        Effect.DENY,
        conditions=[FakeCondition("subject.department", Operator.EQUALS, ["finance"])],
    )

    assert _evaluate([policy], AttributeBag({})).outcome is Outcome.NOT_APPLICABLE


def test_present_distinguishes_absent_from_null() -> None:
    policy = FakePolicy(
        "p", Effect.ALLOW, conditions=[FakeCondition("subject.tag", Operator.PRESENT)]
    )

    assert _evaluate([policy], AttributeBag({"subject.tag": None})).allowed is True
    assert _evaluate([policy], AttributeBag({})).allowed is False


def test_absent_matches_only_when_unresolved() -> None:
    policy = FakePolicy(
        "p", Effect.ALLOW, conditions=[FakeCondition("subject.tag", Operator.ABSENT)]
    )

    assert _evaluate([policy], AttributeBag({})).allowed is True
    assert _evaluate([policy], AttributeBag({"subject.tag": None})).allowed is False


def test_unknown_operator_makes_the_policy_inapplicable() -> None:
    """One malformed row must not break every decision -- fail closed on it."""
    policy = FakePolicy(
        "p", Effect.ALLOW, conditions=[FakeCondition("subject.id", "dominates", ["x"])]
    )

    decision = _evaluate([policy], AttributeBag({"subject.id": str(SUBJECT)}))

    assert decision.outcome is Outcome.NOT_APPLICABLE
    assert "unknown operator" in decision.traces[0].reason


def test_operator_without_required_operands_is_inapplicable() -> None:
    policy = FakePolicy(
        "p", Effect.ALLOW, conditions=[FakeCondition("subject.id", Operator.EQUALS, [])]
    )

    assert _evaluate([policy], AttributeBag({"subject.id": "x"})).allowed is False


# --- Explainability ------------------------------------------------------


def test_decision_records_why_each_policy_did_or_did_not_apply() -> None:
    policies = [
        FakePolicy("applies", Effect.ALLOW),
        FakePolicy("wrong-action", Effect.ALLOW, action="delete"),
    ]

    decision = _evaluate(policies)

    reasons = {trace.policy_name: trace.applied for trace in decision.traces}
    assert reasons == {"applies": True, "wrong-action": False}
    assert len(decision.deciding_policies) == 1


# --- Combining algorithms ------------------------------------------------


def test_deny_overrides_is_registered_by_default() -> None:
    assert "deny_overrides" in registered_combining_algorithms()
    assert get_combining_algorithm("deny_overrides") is deny_overrides


def test_unknown_combining_algorithm_raises() -> None:
    with pytest.raises(ValueError, match="Unknown combining algorithm"):
        _evaluate([], algorithm="first_applicable")


def test_a_combining_algorithm_can_be_registered() -> None:
    """The extension point for A-04, exercised without defining a business rule."""

    def allow_only(traces) -> Outcome:
        return Outcome.ALLOW

    register_combining_algorithm("test_allow_only", allow_only, replace=True)

    assert _evaluate([], algorithm="test_allow_only").outcome is Outcome.ALLOW


def test_registering_a_duplicate_algorithm_is_refused() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_combining_algorithm("deny_overrides", deny_overrides)
