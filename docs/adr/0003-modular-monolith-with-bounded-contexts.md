# ADR-0003 — Modular monolith with bounded contexts

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

APEX spans thirteen bounded contexts — identity, policy, ontology, assets,
evidence, provenance, governance, audit, graph, search, integration,
intelligence and AI. The obvious question at this scale is whether those become
separate services.

The platform's central invariants are cross-cutting, which is what makes the
answer non-obvious:

- A permission check spans **identity, policy, assets** and writes to **audit**.
- A gate transition spans **governance, evidence, assets** and writes to
  **audit**.
- Publishing an asset must atomically move lifecycle state, record the gate
  decision, and emit an integration event.

Enforced across process boundaries, each of these needs either a distributed
transaction or an accepted window in which the invariant is violated. For a
platform whose purpose is *provable* governance, "the audit record usually
lands" is not a property we can offer.

Against that: the ingestion and AI workloads genuinely differ from request
handling. A stuck PDF extraction or a slow model call should not consume
capacity needed to serve a search.

## Decision

APEX is deployed as a **modular monolith**: one API service containing all
bounded contexts, separated in code and schema rather than across a network.

- One Python package per context under `backend/app/`.
- Models are private to their package; the service interface is the public
  surface.
- Dependencies point in one direction only (see
  [domain boundaries](../architecture/domain-boundaries.md)). No upward or
  lateral imports.
- An import-linter contract in CI fails any violation, starting at Commit 005.

Two workloads are separated into their own processes, because their failure and
scaling profiles differ rather than because their data does:

- **Ingestion Worker** (Commit 019)
- **AI Service** (Commit 026)

Both read through the same authorisation layer as the API.

**A context is extracted into a service only when** it needs independent
scaling that the shared process demonstrably cannot provide, *and* its
invariants do not span a boundary, *and* an eventually-consistent contract is
acceptable to the governance model. Those conditions are testable; "it feels
too big" is not.

## Consequences

**Positive**

- Cross-context invariants are enforced in a single database transaction.
- A gate transition and its audit record commit together or not at all.
- One deployment, one migration sequence, one place to reproduce a bug.
- Boundaries are real but cheap to adjust while the domain model is still
  moving.

**Negative**

- Contexts cannot scale independently. Mitigated by extracting the two
  workloads that actually need it.
- Module boundaries are enforced by tooling rather than by the network, so the
  import contract must be maintained or the structure erodes.
- The whole service redeploys for a change to any context.

**Neutral**

- Nothing here prevents later extraction. Service interfaces and integration
  events are exactly the seams extraction would need, which is why they exist
  from the start rather than being retrofitted.
