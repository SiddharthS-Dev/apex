"""The integration event envelope.

An envelope is what every consumer can rely on regardless of what the event
*says*. Its fields are the ones a consumer needs to route, de-duplicate, trace
and reason about an event without understanding its payload.

**Envelope version and event version are different things**, and conflating
them is a common and expensive mistake:

``envelope_version``
    The version of *this structure*. Changes when the envelope itself gains or
    loses a field. A consumer checks it to know how to parse anything at all.

``event_version``
    The version of one event type's payload. ``asset.published`` v1 and v2
    carry different payloads inside an identical envelope.

**No domain event types are registered here.** The registry ships empty. Naming
``asset.published`` before the requirements define what an asset is would put a
guess into a contract that other systems then depend on -- the most expensive
possible place for one. See :mod:`app.events.registry`.

**No ordering guarantee.** Events are not globally ordered, not ordered per
tenant, and not ordered per aggregate. Nothing in the requirements specifies an
ordering guarantee, so none is offered: a consumer that needs order must derive
it from ``occurred_at`` and its own state, and must tolerate arriving out of
order. Promising an order the outbox does not actually provide would be worse
than promising none.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.correlation import correlation_id_or_new, current_causation_id
from app.core.redaction import redact

#: Version of the envelope structure itself. Bumped when a field is added or
#: removed, never when a payload changes.
ENVELOPE_VERSION = 1

#: Identifies this platform as the producer. A consumer reading from several
#: systems needs to know which one spoke.
DEFAULT_PRODUCER = "apex"

#: Hard ceiling on a serialised payload. An outbox row is read repeatedly by
#: pollers; an unbounded payload turns that into a bandwidth problem, and an
#: event is meant to be a notification rather than a data transfer.
MAX_PAYLOAD_BYTES = 256 * 1024

#: Event type names are dot-separated lowercase segments, e.g. ``a.b.c``.
_EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*){1,4}$"


class EventValidationError(ValueError):
    """An envelope could not be built or parsed."""


class EventEnvelope(BaseModel):
    """A transport-independent integration event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    envelope_version: int = ENVELOPE_VERSION

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str = Field(min_length=3, max_length=128, pattern=_EVENT_TYPE_PATTERN)
    event_version: int = Field(default=1, ge=1)

    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    #: Which tenant the event belongs to. Supplied by the producer from the
    #: authenticated context -- never read back out of the payload, which is
    #: why :meth:`_reject_tenant_in_payload` exists.
    tenant_id: uuid.UUID

    actor_type: str | None = Field(default=None, max_length=32)
    actor_id: uuid.UUID | None = None

    correlation_id: uuid.UUID = Field(default_factory=correlation_id_or_new)
    causation_id: uuid.UUID | None = Field(default_factory=current_causation_id)

    producer: str = Field(default=DEFAULT_PRODUCER, max_length=64)
    #: Which bounded context emitted it, e.g. ``identity``.
    source_context: str | None = Field(default=None, max_length=64)

    #: Deterministic de-duplication handle. Two enqueues with the same key in
    #: one tenant are the same event, however many times they are submitted.
    idempotency_key: str = Field(min_length=1, max_length=255)

    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """A naive timestamp is ambiguous the moment it crosses a boundary."""
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _reject_tenant_in_payload(self) -> Self:
        """Refuse a payload that restates tenancy.

        Tenant identity comes from the envelope, which the producer set from
        authenticated context. A ``tenant_id`` in the payload is either
        redundant or an attempt to have a consumer trust the wrong one; either
        way it must not exist.
        """
        for key in self.payload:
            if key.lower() in {"tenant_id", "tenantid", "tenant"}:
                raise ValueError(
                    "Payload must not carry tenant identity; the envelope owns it"
                )
        return self

    @model_validator(mode="after")
    def _enforce_payload_size(self) -> Self:
        size = len(json.dumps(self.payload, default=str).encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"Payload is {size} bytes; the limit is {MAX_PAYLOAD_BYTES}"
            )
        return self


def build_envelope(
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    event_version: int = 1,
    actor_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    source_context: str | None = None,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> EventEnvelope:
    """Build a validated envelope.

    The payload is **redacted here**, not at the call site. An event leaves the
    process, so a credential copied in from request context reaches whatever
    consumes the outbox -- and unlike a log line, it cannot be recalled.
    """
    try:
        return EventEnvelope(
            tenant_id=tenant_id,
            event_type=event_type,
            event_version=event_version,
            idempotency_key=idempotency_key,
            # No truncation: the envelope enforces a total size limit, and a
            # consumer acting on a silently shortened value is worse than a
            # producer refusing an oversized one.
            payload=redact(payload, max_value_length=None),
            actor_type=actor_type,
            actor_id=actor_id,
            source_context=source_context,
            correlation_id=correlation_id or correlation_id_or_new(),
            causation_id=causation_id if causation_id is not None else current_causation_id(),
            occurred_at=occurred_at or datetime.now(UTC),
        )
    except ValueError as exc:
        raise EventValidationError(str(exc)) from exc


def serialise(envelope: EventEnvelope) -> str:
    """Render an envelope as JSON for transport."""
    return envelope.model_dump_json()


def deserialise(raw: str | bytes) -> EventEnvelope:
    """Parse an envelope, rejecting anything that does not validate.

    An unknown ``envelope_version`` is refused rather than best-effort parsed:
    guessing at a structure written by newer code is how a consumer silently
    drops a field that mattered.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(f"Event is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise EventValidationError("Event must be a JSON object")

    version = data.get("envelope_version")
    if version != ENVELOPE_VERSION:
        raise EventValidationError(
            f"Unsupported envelope version {version!r}; this build reads "
            f"version {ENVELOPE_VERSION}"
        )

    try:
        return EventEnvelope.model_validate(data)
    except ValueError as exc:
        raise EventValidationError(str(exc)) from exc
