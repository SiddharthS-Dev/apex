# APEX architecture

> **Scope of this document.** This is the foundation-level overview written
> alongside the initial scaffold. The full C4 context, container and component
> diagrams, the domain and authority boundary maps, and the remaining
> architecture decision records are delivered in the next increment
> (*docs: add apex system architecture*).

---

## Shape of the system

APEX is a three-tier application with a clear separation between the
presentation client, a single API service, and the stores behind it.

- **Frontend** — React + TypeScript single-page application. Holds no
  authority: every access decision is made server-side.
- **Backend** — FastAPI service, organised by bounded context rather than by
  technical layer. This is where authorization, evidence rules and lifecycle
  transitions are enforced.
- **PostgreSQL** — the system of record. Relational data, full-text search and
  vector embeddings (`pgvector`) live in one store, which keeps retrieval
  filtering and permission filtering in the same query plane.
- **Object storage** — S3-compatible, for asset binaries. The database holds
  metadata, content hashes and references; never the bytes.

---

## Bounded contexts

Each context owns its tables and exposes a service interface. Cross-context
access goes through that interface or through an integration event — never a
direct table read.

| Context | Responsibility |
| --- | --- |
| Identity | Users, roles, permissions, authentication |
| Policy | Attribute-based access rules: tenant, organisation, audience, geography, classification |
| Ontology | The shared vocabulary everything else references |
| Assets | Asset entities, versions, metadata, lifecycle state |
| Evidence | Claims, authorities, confidence, validity windows |
| Provenance | Sources, version history, trace graphs |
| Governance | Gates G0–G6, review and approval queues, SLAs, escalation |
| Audit | Immutable event log |
| Graph | Nodes, relationships, authorization-aware traversal |
| Search | Keyword, vector, hybrid retrieval |
| Integration | Event contracts and external system adapters |
| Intelligence | KPIs, command centre, trend radar |
| AI | Grounded retrieval, model/prompt/tool registries, bounded agents |

---

## Authority boundaries

Two rules constrain the design and are worth stating before any code depends
on them.

**1. APEX does not duplicate authoritative records.**
Where another system is the source of truth — IVEOS, Vanguard, CK/ESG-AIoT,
Meris, Helix — APEX holds a reference and reacts to events. It does not hold a
second copy that can silently diverge.

**2. Authorization is evaluated at retrieval, not at presentation.**
Search results, graph traversals and AI context assembly are all filtered by
the same policy evaluation. A user cannot reach content through the graph or
through an agent that they could not reach directly.

---

## Cross-cutting concerns

| Concern | Approach |
| --- | --- |
| Authorization | RBAC for coarse capability, ABAC for row- and field-level scope |
| Audit | Every state change writes an immutable event with actor, resource, before/after and correlation ID |
| Provenance | Assets carry their source and evidence chain; claims without evidence do not publish |
| Observability | Correlation IDs propagate from request through events to agent executions |
| Configuration | Environment-driven, `APEX_`-prefixed, declared in `.env.example` |

---

## Decision records

| ADR | Decision |
| --- | --- |
| [0001](../adr/0001-record-architecture-decisions.md) | Record architecture decisions |
| [0002](../adr/0002-technology-stack.md) | Technology stack |
