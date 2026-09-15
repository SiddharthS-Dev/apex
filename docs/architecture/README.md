# APEX architecture

Documentation for the APEX platform architecture, organised as a
[C4 model](https://c4model.com) — context, containers, components — plus the
boundary maps that constrain all three.

---

## Read in this order

| # | Document | What it answers |
| --- | --- | --- |
| 1 | [System context](context.md) | Who uses APEX, and what it depends on |
| 2 | [Containers](containers.md) | What is deployed, and how the pieces talk |
| 3 | [Components](components.md) | What is inside the API service |
| 4 | [Domain boundaries](domain-boundaries.md) | Which context owns what, and how to cross |
| 5 | [Authority boundaries](authority-boundaries.md) | Which system or role may assert what |
| 6 | [Assumptions](assumptions.md) | What was inferred, and what must be reconciled |
| — | [Decision records](../adr/README.md) | Why the load-bearing choices were made |

---

## The four guarantees

Everything in these documents exists to make four statements structurally true
— properties of the design, not outcomes of careful review.

| Guarantee | Mechanism | Recorded in |
| --- | --- | --- |
| A user cannot reach content they are not entitled to, by **any** path — direct, search, graph or AI | Authorisation is evaluated inside the query as a scope predicate; unauthorised rows are never loaded | [ADR-0004](../adr/0004-authorization-at-retrieval.md) |
| A published claim is always backed by valid evidence | Evidence is a precondition of gate G2, and G4 requires G2 | [ADR-0005](../adr/0005-evidence-gated-publication.md) |
| The authoritative source of any value is answerable | APEX references upstream records and reacts to events; it never copies them | [ADR-0006](../adr/0006-integration-by-reference-and-event.md) |
| What happened can be reconstructed and trusted | Append-only audit log, written in the same transaction as the change | [ADR-0007](../adr/0007-append-only-audit-log.md) |

All four depend on the contexts sharing one process and one transaction
boundary — see [ADR-0003](../adr/0003-modular-monolith-with-bounded-contexts.md).

---

## Shape of the system

- **Frontend** — React + TypeScript single-page application. Holds no
  authority: every access decision is made server-side.
- **Backend** — FastAPI service organised by bounded context rather than by
  technical layer. This is where authorisation, evidence rules and lifecycle
  transitions are enforced.
- **PostgreSQL** — the system of record. Relational data, full-text search and
  vector embeddings (`pgvector`) in one store, which keeps retrieval filtering
  and permission filtering in the same query plane.
- **Object storage** — S3-compatible, for asset binaries. The database holds
  metadata, content hashes and references; never the bytes.

---

## Bounded contexts at a glance

| Context | Responsibility |
| --- | --- |
| Identity | Users, roles, permissions, authentication |
| Policy | ABAC attributes: tenant, organisation, audience, geography, classification |
| Ontology | The shared vocabulary everything else references |
| Assets | Asset entities, versions, metadata, lifecycle state |
| Evidence | Claims, authorities, confidence, validity windows |
| Provenance | Sources, version history, trace chains |
| Governance | Gates G0–G6, review and approval queues, SLAs, escalation |
| Audit | Immutable event log |
| Graph | Nodes, relationships, authorisation-aware traversal |
| Search | Keyword, vector and hybrid retrieval |
| Integration | Event contracts and external system adapters |
| Intelligence | KPIs, command centre, trend radar |
| AI | Grounded retrieval, registries, bounded agents |

Full ownership and dependency rules: [domain boundaries](domain-boundaries.md).

---

## A note on what is missing

These documents describe the architecture that the available specification
supports. Where requirements are not yet known — ontology attributes, gate
authority mapping, tenancy isolation, the twelve trends, the ten APEX modules —
the documents are **deliberately silent** rather than speculative.

Twenty-two such gaps are tracked in [assumptions.md](assumptions.md), each with
its blast radius if the assumption proves wrong, plus a note on which parts of
the platform are deliberately framework-only until the specification lands.

Two are resolved: the tenancy isolation model
([ADR-0008](../adr/0008-shared-schema-multi-tenancy.md)) and the authentication
approach ([ADR-0009](../adr/0009-local-first-authentication.md)). The next
blocker is **A-02**, the ontology entity model, which Commit 006 needs.
