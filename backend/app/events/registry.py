"""The event type registry -- deliberately empty.

**No domain event type is registered.** That is the point of this module, not
an omission.

The development plan lists seven event names for Commit 020. They are recorded
below as *documentation of intent*, and none of them is registered:

===============================  ==================================
``release.evidence.approved``    Needs Evidence (A-02) and gate authority (A-04)
``outcome.verified``             Needs the outcome model; unspecified
``asset.published``              Needs Asset (A-02) and gate G4 (A-04)
``content.performance.updated``  Needs the KPI model (A-10)
``gate.updated``                 Needs the gate authority matrix (A-04)
``learning.asset.released``      Needs Asset (A-02) and the Academy module (A-09)
``asset.expiry.due``             Needs Asset (A-02) and retention rules (A-11)
===============================  ==================================

An integration event is a **contract with systems outside APEX**. Once
``asset.published`` is emitted with a payload, every consumer depends on that
shape, and changing it means coordinating a migration across systems we do not
control. A name and payload guessed before the requirements define what an
asset *is* would be the most expensive kind of guess available -- far more so
than a wrong table, which is ours alone to migrate.

So the framework ships complete and the catalogue ships empty. Registering a
type later is a few lines here plus a payload model; nothing in the envelope,
the outbox, or any consumer needs to change.

**Registration is advisory, not a gate.** Like the audit catalogue, the outbox
accepts an unregistered event type. A framework that refuses to carry an event
because a catalogue entry is missing fails exactly when a new context ships.
:func:`is_registered` lets a caller check; nothing forces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Event names the development plan anticipates. **Documentation only** -- none
#: is registered, and none should be until the master prompt defines its
#: payload and semantics.
PLANNED_EVENT_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("release.evidence.approved", "Blocked on A-02 (evidence) and A-04 (gates)"),
    ("outcome.verified", "Blocked: the outcome model is unspecified"),
    ("asset.published", "Blocked on A-02 (asset) and A-04 (gate G4)"),
    ("content.performance.updated", "Blocked on A-10 (KPI definitions)"),
    ("gate.updated", "Blocked on A-04 (gate authority matrix)"),
    ("learning.asset.released", "Blocked on A-02 and A-09 (Academy module)"),
    ("asset.expiry.due", "Blocked on A-02 and A-11 (retention rules)"),
)


@dataclass(frozen=True, slots=True)
class EventTypeSpec:
    """A registered event type and the payload version it is current at."""

    event_type: str
    current_version: int
    description: str


_REGISTRY: dict[str, EventTypeSpec] = {}


def register_event_type(spec: EventTypeSpec, *, replace: bool = False) -> None:
    """Register an event type.

    The extension point for the master prompt's event catalogue.
    """
    if not replace and spec.event_type in _REGISTRY:
        raise ValueError(f"Event type {spec.event_type!r} is already registered")
    _REGISTRY[spec.event_type] = spec


def get_event_type(event_type: str) -> EventTypeSpec | None:
    """Look up a registered event type, or ``None``."""
    return _REGISTRY.get(event_type)


def registered_event_types() -> frozenset[str]:
    """Every registered event type. Empty until the requirements land."""
    return frozenset(_REGISTRY)


def is_registered(event_type: str) -> bool:
    """Whether an event type is catalogued. Informational only."""
    return event_type in _REGISTRY


def planned_event_types() -> frozenset[str]:
    """Names the plan anticipates but which are **not** implemented."""
    return frozenset(name for name, _ in PLANNED_EVENT_TYPES)
