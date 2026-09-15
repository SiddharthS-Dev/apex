"""Audit bounded context: the immutable record of what happened.

Shaped differently from every other context, deliberately. Audit is written to
by all of them and depends on none, and **no domain logic reads it**. The moment
a business decision derives from audit history, the log stops being a record of
behaviour and becomes an input to it -- its schema can no longer evolve, old
partitions can no longer be archived, and a gap in it becomes a correctness bug
rather than an observability one.

See [ADR-0007](../../../docs/adr/0007-append-only-audit-log.md).
"""
