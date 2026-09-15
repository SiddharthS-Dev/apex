"""Event envelope, serialisation and registry. No database required."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.correlation import correlation_scope
from app.core.redaction import REDACTED
from app.events import registry
from app.events.envelope import (
    ENVELOPE_VERSION,
    MAX_PAYLOAD_BYTES,
    EventEnvelope,
    EventValidationError,
    build_envelope,
    deserialise,
    serialise,
)

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")


def _envelope(**overrides: object) -> EventEnvelope:
    kwargs: dict[str, object] = {
        "tenant_id": TENANT,
        "event_type": "example.thing.happened",
        "idempotency_key": "key-1",
    }
    kwargs.update(overrides)
    return build_envelope(**kwargs)  # type: ignore[arg-type]


# --- Construction --------------------------------------------------------


def test_envelope_carries_the_required_fields() -> None:
    envelope = _envelope()

    assert envelope.envelope_version == ENVELOPE_VERSION
    assert envelope.event_type == "example.thing.happened"
    assert envelope.event_version == 1
    assert envelope.tenant_id == TENANT
    assert envelope.producer == "apex"
    assert envelope.event_id


def test_each_envelope_gets_a_distinct_id() -> None:
    assert _envelope().event_id != _envelope().event_id


def test_envelope_is_immutable() -> None:
    """A consumer must not be able to alter what it was handed."""
    envelope = _envelope()

    with pytest.raises(ValueError):
        envelope.event_type = "something.else"  # type: ignore[misc]


def test_timestamp_is_timezone_aware() -> None:
    assert _envelope().occurred_at.tzinfo is not None


def test_naive_timestamp_is_refused() -> None:
    """A naive timestamp is ambiguous the moment it crosses a boundary."""
    with pytest.raises(EventValidationError, match="timezone-aware"):
        _envelope(occurred_at=datetime(2026, 1, 1))  # noqa: DTZ001


# --- Versioning ----------------------------------------------------------


def test_event_version_is_separate_from_envelope_version() -> None:
    """Conflating them is how a payload change breaks envelope parsing."""
    envelope = _envelope(event_version=3)

    assert envelope.event_version == 3
    assert envelope.envelope_version == ENVELOPE_VERSION


def test_event_version_must_be_positive() -> None:
    with pytest.raises(EventValidationError):
        _envelope(event_version=0)


def test_unknown_envelope_version_is_refused_not_guessed() -> None:
    """Best-effort parsing of a newer structure silently drops fields."""
    raw = json.loads(serialise(_envelope()))
    raw["envelope_version"] = 99

    with pytest.raises(EventValidationError, match="Unsupported envelope version"):
        deserialise(json.dumps(raw))


# --- Event type naming ---------------------------------------------------


def test_event_type_must_be_dotted_lowercase() -> None:
    for bad in ("Nope", "no-dots", "UPPER.CASE", "a", "trailing.", ".leading"):
        with pytest.raises(EventValidationError):
            _envelope(event_type=bad)


def test_reasonable_event_types_are_accepted() -> None:
    for good in ("a.b", "identity.user.created", "a.b.c.d.e"):
        assert _envelope(event_type=good).event_type == good


# --- Serialisation -------------------------------------------------------


def test_envelope_round_trips_through_json() -> None:
    original = _envelope(payload={"n": 1, "s": "x"}, source_context="identity")

    restored = deserialise(serialise(original))

    assert restored == original


def test_malformed_json_is_refused() -> None:
    with pytest.raises(EventValidationError, match="not valid JSON"):
        deserialise("{not json")


def test_non_object_json_is_refused() -> None:
    with pytest.raises(EventValidationError, match="must be a JSON object"):
        deserialise("[1, 2, 3]")


def test_unknown_fields_are_refused() -> None:
    """Extra fields mean the sender and reader disagree; fail rather than drop."""
    raw = json.loads(serialise(_envelope()))
    raw["surprise"] = "value"

    with pytest.raises(EventValidationError):
        deserialise(json.dumps(raw))


def test_missing_required_field_is_refused() -> None:
    raw = json.loads(serialise(_envelope()))
    del raw["tenant_id"]

    with pytest.raises(EventValidationError):
        deserialise(json.dumps(raw))


# --- Tenant integrity ----------------------------------------------------


def test_payload_may_not_restate_tenancy() -> None:
    """Tenant comes from the envelope, set from authenticated context."""
    for key in ("tenant_id", "tenantId", "TENANT"):
        with pytest.raises(EventValidationError, match="tenant identity"):
            _envelope(payload={key: str(uuid.uuid4())})


def test_payload_may_carry_other_identifiers() -> None:
    envelope = _envelope(payload={"organisation_id": "x", "user_id": "y"})

    assert envelope.payload["organisation_id"] == "x"


# --- Payload safety ------------------------------------------------------


def test_payload_is_redacted_on_construction() -> None:
    """An event leaves the process; a credential in one cannot be recalled."""
    envelope = _envelope(payload={"password": "hunter2", "note": "kept"})

    assert envelope.payload["password"] == REDACTED
    assert envelope.payload["note"] == "kept"


def test_nested_credentials_are_redacted() -> None:
    envelope = _envelope(payload={"outer": {"access_token": "abc", "id": "1"}})

    assert envelope.payload["outer"]["access_token"] == REDACTED
    assert envelope.payload["outer"]["id"] == "1"


def test_oversized_payload_is_refused() -> None:
    with pytest.raises(EventValidationError, match="limit is"):
        _envelope(payload={"blob": "x" * (MAX_PAYLOAD_BYTES + 1000)})


def test_payload_within_the_limit_is_accepted() -> None:
    assert _envelope(payload={"blob": "x" * 1000}).payload["blob"]


def test_idempotency_key_is_required() -> None:
    with pytest.raises(EventValidationError):
        _envelope(idempotency_key="")


# --- Correlation ---------------------------------------------------------


def test_correlation_id_is_taken_from_scope() -> None:
    correlation = uuid.uuid4()

    with correlation_scope(correlation):
        envelope = _envelope()

    assert envelope.correlation_id == correlation


def test_causation_id_is_carried_from_scope() -> None:
    cause = uuid.uuid4()

    with correlation_scope(uuid.uuid4(), causation_id=cause):
        envelope = _envelope()

    assert envelope.causation_id == cause


def test_correlation_id_survives_serialisation() -> None:
    correlation = uuid.uuid4()
    with correlation_scope(correlation):
        envelope = _envelope()

    assert deserialise(serialise(envelope)).correlation_id == correlation


def test_explicit_correlation_overrides_scope() -> None:
    explicit = uuid.uuid4()

    with correlation_scope(uuid.uuid4()):
        envelope = _envelope(correlation_id=explicit)

    assert envelope.correlation_id == explicit


def test_an_envelope_without_scope_still_gets_a_correlation_id() -> None:
    """An isolated id beats none at all."""
    assert _envelope().correlation_id is not None


# --- Registry ------------------------------------------------------------


def test_no_domain_event_type_is_registered() -> None:
    """The decisive test: the catalogue must ship empty (A-02, A-04)."""
    assert registry.registered_event_types() == frozenset()


def test_planned_event_types_are_documented_but_not_registered() -> None:
    planned = registry.planned_event_types()

    assert "asset.published" in planned
    assert "gate.updated" in planned
    for name in planned:
        assert registry.is_registered(name) is False
        assert registry.get_event_type(name) is None


def test_an_event_type_can_be_registered_later() -> None:
    """The extension point, exercised without naming a domain event."""
    spec = registry.EventTypeSpec("test.example.happened", 1, "A test type")
    registry.register_event_type(spec, replace=True)

    assert registry.is_registered("test.example.happened")
    assert registry.get_event_type("test.example.happened") == spec

    registry._REGISTRY.pop("test.example.happened", None)


def test_registering_a_duplicate_is_refused() -> None:
    spec = registry.EventTypeSpec("test.duplicate", 1, "x")
    registry.register_event_type(spec, replace=True)
    try:
        with pytest.raises(ValueError, match="already registered"):
            registry.register_event_type(spec)
    finally:
        registry._REGISTRY.pop("test.duplicate", None)


def test_an_unregistered_type_still_builds_an_envelope() -> None:
    """A catalogue gap must not stop a new context emitting events."""
    envelope = _envelope(event_type="brand.new.thing")

    assert registry.is_registered("brand.new.thing") is False
    assert envelope.event_type == "brand.new.thing"


# --- Ordering ------------------------------------------------------------


def test_no_sequence_number_is_offered() -> None:
    """No ordering is guaranteed, so no field implies one."""
    fields = set(EventEnvelope.model_fields)

    assert not fields & {"sequence", "sequence_number", "offset", "position", "version_vector"}


def test_timestamps_alone_do_not_imply_order() -> None:
    """Two events can share a timestamp; consumers must tolerate it."""
    moment = datetime.now(UTC)
    first = _envelope(idempotency_key="a", occurred_at=moment)
    second = _envelope(idempotency_key="b", occurred_at=moment)

    assert first.occurred_at == second.occurred_at
    assert first.event_id != second.event_id


def test_occurred_at_can_predate_enqueue() -> None:
    earlier = datetime.now(UTC) - timedelta(hours=1)

    assert _envelope(occurred_at=earlier).occurred_at == earlier
