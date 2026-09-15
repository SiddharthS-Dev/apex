# ADR-0007 — Append-only audit log

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

APEX must be able to reconstruct who did what, when, and on what basis — for
governance decisions, publication, rights, and access to classified content.
An audit log that can be modified cannot serve that purpose: if a record can be
edited, its value as evidence is exactly zero, because no reader can distinguish
an accurate log from a tidied one.

There is also a subtler failure to avoid. Once an audit log is queryable by
application code, it becomes tempting to *use* it — "has this been approved
before?", "when did this last change?" The moment domain logic reads the log,
the log stops being a record of behaviour and becomes an input to behaviour.
Its schema then cannot evolve, it cannot be archived, and a gap in it becomes a
correctness bug rather than an observability one.

## Decision

The audit log is **append-only**, and **no domain logic reads it**.

1. **Writes only.** The audit context exposes an append operation. No update or
   delete path exists in the service, the repository, or the API.
2. **Every state change is audited.** Lifecycle transitions, gate decisions,
   evidence changes, policy and role changes, and access to classified content.
3. **Each event records:** actor, resource, action, before state, after state,
   correlation ID, causation ID, timestamp, IP address, user agent.
4. **Audit depends on nothing.** It sits outside the context dependency graph
   and is written to by every context — the inverted shape in
   [domain boundaries](../architecture/domain-boundaries.md).
5. **No domain logic reads it.** Business decisions derive from domain state,
   never from audit history. The log is read by humans and by the audit UI.
6. **The write is in the transaction.** A gate decision and its audit record
   commit together or not at all — one of the invariants that
   [ADR-0003](0003-modular-monolith-with-bounded-contexts.md) keeps inside a
   single process.
7. **Database permissions match.** The application role is granted `INSERT` and
   `SELECT` on audit tables; `UPDATE` and `DELETE` are not granted, so
   application-level immutability is backed at the database.

## Consequences

**Positive**

- The log is usable as evidence, because it cannot have been edited.
- Correlation IDs let one user action be traced across contexts, integration
  events and agent executions.
- Before/after state makes reconstruction possible without replaying
  application logic.
- Because nothing depends on reading it, the schema can evolve and old
  partitions can be archived without breaking behaviour.

**Negative**

- The log grows without bound and needs a partitioning and archival strategy.
  Archived partitions must remain readable to stay useful.
- Writes are on the transaction path of every state change, so the insert must
  stay cheap — no synchronous fan-out, no derived aggregates computed inline.
- An incorrect audit entry cannot be corrected, only followed by a compensating
  entry. This is the intended trade.
- Personal data in audit records (actor, IP, user agent) is subject to
  retention rules that conflict with immutability. Resolving that needs a
  specified retention policy — see **A-11** in
  [assumptions.md](../architecture/assumptions.md).

**Neutral**

- Cryptographic chaining of entries (hash-linking each record to its
  predecessor) would make tampering detectable, not merely disallowed. Not
  adopted now: it adds write cost and key management for a threat model that
  has not been stated. Revisit if external attestation becomes a requirement.
