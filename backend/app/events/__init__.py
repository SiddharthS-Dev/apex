"""Integration events: the contract with systems outside APEX.

Three pieces, and one guarantee.

:mod:`app.events.envelope`
    A versioned envelope every consumer can rely on regardless of payload:
    identity, type and version, timestamp, tenant, actor, correlation and
    causation, producer, and an idempotency key.

:mod:`app.events.registry`
    The event type catalogue. **Ships empty**, on purpose -- see below.

:mod:`app.events.outbox`
    Durable, transport-independent delivery with retries, dead-lettering and
    replay.

**The guarantee:** if the originating transaction commits, the event exists; if
it rolls back, the event never existed. Achieved by writing the event as a row
in that same transaction rather than by calling a broker, because a broker
publish inside a transaction can succeed while the transaction rolls back.

**Framework only.** No domain event type is registered. An integration event is
a contract with systems outside APEX -- once ``asset.published`` is emitted with
a payload, consumers we do not control depend on that shape. Guessing it before
the requirements define what an asset is would be the most expensive guess
available. The seven names the plan anticipates are recorded in
:mod:`app.events.registry` as planned and unregistered.

**Not an audit log.** Audit records what happened for accountability, is
immutable, and never leaves the tenant. Integration events tell other systems
something happened, are deletable once delivered, and cross the boundary. They
share correlation and causation ids so a chain can be traced across both, and
nothing else.

**No ordering guarantee** -- not global, not per tenant, not per aggregate.
Delivery is at-least-once, and ``event_id`` is what consumers de-duplicate on.
"""
