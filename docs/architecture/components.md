# C4 Level 3 — Components

Inside the API service.

The organising principle is that **every request passes through the same
enforcement pipeline** before it reaches domain logic, and **every state change
leaves an audit record**. Components exist to make those two statements
structurally true rather than a matter of developer discipline.

---

```mermaid
---
config:
  theme: base
  look: classic
  themeVariables:
    fontFamily: Segoe UI, Calibri, Arial
    fontSize: 15px
    background: "#FFFDF6"
    primaryColor: "#EAF5E2"
    primaryTextColor: "#1F2937"
    primaryBorderColor: "#4CAF50"
    secondaryColor: "#FFF7CC"
    tertiaryColor: "#F6F2FF"
    lineColor: "#555555"
    textColor: "#222222"
    edgeLabelBackground: "#FFFDF6"
    clusterBkg: "#FFF9D6"
    clusterBorder: "#D9C97C"
    nodeBorder: "#888888"
---
flowchart TB
    client["Web Application"]

    subgraph apiservice["API Service"]
        direction TB

        subgraph edge["Request pipeline — every request, no exceptions"]
            direction TB
            router["Routers<br/><i>HTTP surface per context</i>"]
            authn["Authentication<br/><i>Token validation, principal resolution</i>"]
            pdp["Policy Decision Point<br/><i>RBAC capability + ABAC scope</i>"]
            corr["Correlation<br/><i>Request ID propagation</i>"]
        end

        subgraph domain["Domain services — one per bounded context"]
            direction TB
            assetsvc["Asset Service<br/><i>+ lifecycle state machine</i>"]
            evidsvc["Evidence Service<br/><i>claims, authorities, validity</i>"]
            govsvc["Governance Service<br/><i>gates G0-G6, queues, SLA</i>"]
            ontosvc["Ontology Service"]
            graphsvc["Graph Service<br/><i>authorisation-aware traversal</i>"]
            searchsvc["Retrieval Service<br/><i>keyword + vector + graph</i>"]
        end

        subgraph platform["Platform services"]
            direction TB
            auditsvc["Audit Writer<br/><i>append-only</i>"]
            eventsvc["Event Publisher<br/><i>outbox</i>"]
            provsvc["Provenance Service"]
        end

        subgraph persistence["Persistence"]
            direction TB
            repo["Repositories<br/><i>SQLAlchemy - policy filter applied here</i>"]
            migrations["Alembic<br/><i>schema migrations</i>"]
        end
    end

    db[("PostgreSQL")]

    client -->|"JSON + bearer token"| router
    router --> authn
    authn --> pdp
    pdp --> corr
    corr --> domain

    assetsvc --> repo
    evidsvc --> repo
    govsvc --> repo
    ontosvc --> repo
    graphsvc --> repo
    searchsvc --> repo

    assetsvc -.->|"every transition"| auditsvc
    govsvc -.->|"every decision"| auditsvc
    evidsvc -.->|"every claim change"| auditsvc

    govsvc -.->|"gate.updated"| eventsvc
    assetsvc -.->|"asset.published"| eventsvc

    assetsvc --> provsvc
    evidsvc --> provsvc

    auditsvc --> repo
    eventsvc --> repo
    provsvc --> repo

    repo -->|"SQL"| db
    migrations -->|"DDL"| db

    pdp -.->|"scope predicate"| repo

    classDef application fill:#E9F7E7,stroke:#4CAF50,color:#1F2937,stroke-width:2px;
    classDef api         fill:#DDEEFF,stroke:#1E88E5,color:#0F172A,stroke-width:2px;
    classDef security    fill:#F4E8FF,stroke:#7E57C2,color:#311B92,stroke-width:2px;
    classDef service     fill:#E7F5FF,stroke:#2196F3,color:#1F2937,stroke-width:2px;
    classDef monitoring  fill:#FFE8F4,stroke:#E91E63,color:#880E4F,stroke-width:2px;
    classDef queue       fill:#E6F7FF,stroke:#26A69A,color:#004D40,stroke-width:2px;
    classDef process     fill:#E8F7FF,stroke:#42A5F5,color:#0D47A1,stroke-width:2px;
    classDef database    fill:#FFF7C8,stroke:#D4A017,color:#3B2F00,stroke-width:2px;

    class client application;
    class router api;
    class authn,pdp security;
    class corr monitoring;
    class assetsvc,evidsvc,govsvc,ontosvc,graphsvc,searchsvc service;
    class auditsvc monitoring;
    class eventsvc queue;
    class provsvc service;
    class repo,migrations process;
    class db database;

    style edge fill:#F9F1FF,stroke:#D9C97C,stroke-width:1px
    style domain fill:#EEF8FF,stroke:#D9C97C,stroke-width:1px
    style platform fill:#FFF0F8,stroke:#D9C97C,stroke-width:1px
    style persistence fill:#F7F7F7,stroke:#D9C97C,stroke-width:1px
    style apiservice fill:#FCFCFA,stroke:#BFB98A,stroke-width:2px
```

---

## The request pipeline

| Component | Responsibility | Introduced |
| --- | --- | --- |
| **Routers** | HTTP surface, request/response schemas. No business logic. | Commit 001 |
| **Authentication** | Validates the bearer token, resolves the calling principal. Rejects unauthenticated calls before any domain code runs. | Commit 004 |
| **Policy Decision Point** | Evaluates RBAC capability (*may this role do this at all?*) and ABAC scope (*which rows, for this tenant, audience, geography and classification?*). Emits a scope predicate. | Commits 004–005 |
| **Correlation** | Assigns or propagates a correlation ID onto the request, every audit event and every integration event it causes. | Commit 013 |

The PDP does not return a boolean. It returns a **scope predicate** that the
repository layer applies to the query itself. This is the mechanism behind
[ADR-0004](../adr/0004-authorization-at-retrieval.md): unauthorised rows are
never loaded, so they cannot be leaked by a later code path that forgets to
check — including graph traversal and AI context assembly.

### The ABAC pipeline

Policy evaluation is four separable stages, each in its own module, in
dependency order:

| Stage | Module | Responsibility |
| --- | --- | --- |
| **Policy definition** | `app/policy/models.py` | Tenants and rules, as tenant-scoped data. A condition names an attribute key, an operator and literal operands — it does not know what the attribute means. |
| **Attribute resolution** | `app/policy/attributes.py` | Resolvers turn a request into facts, each owning one namespace so two can never contest an attribute. |
| **Policy decision** | `app/policy/engine.py` | A **pure function** of request, attributes and policies. No I/O, no session, no globals beyond two registries. |
| **Enforcement** | `app/policy/enforcement.py` | Turns a decision into an HTTP outcome. Default-deny: anything but `ALLOW` refuses. |

The separation is what makes the evaluator exhaustively testable without a
database or a web framework, and it is also what keeps tenant isolation
intact — the engine never loads anything, so what it sees is entirely
determined by the service layer, which loads under `tenant_scope`.

Two registries are the extension points. New attribute domains arrive as
**registered resolvers**; new comparisons — including any ordering-aware one —
arrive as **registered operators**. Neither requires changing the evaluator or
the schema, which is how classification, audience, geography and organisation
can be added once their semantics are specified.

---

## Domain services

One per bounded context, each owning its own tables. See
[Domain boundaries](domain-boundaries.md) for the full context map and the
rules for crossing between them.

| Component | Responsibility | Introduced |
| --- | --- | --- |
| **Asset Service** | Asset entities, versions, metadata, content hashes. Hosts the lifecycle state machine; transitions are the only way asset state changes. | Commits 007, 009 |
| **Evidence Service** | Claims, evidence references, authorities, confidence, validity windows. Enforces that unevidenced claims cannot publish. | Commit 010 |
| **Governance Service** | Gates G0–G6, review and approval queues, escalation, SLA and expiry. | Commit 012 |
| **Ontology Service** | The shared vocabulary every other context references. | Commit 006 |
| **Graph Service** | Nodes, relationships and traversal — always under the caller's scope predicate. | Commit 014 |
| **Retrieval Service** | Keyword, vector and hybrid retrieval, ranked and permission-filtered. | Commits 016–018 |

---

## Platform services

| Component | Responsibility | Introduced |
| --- | --- | --- |
| **Audit Writer** | Append-only event log: actor, resource, action, before/after, correlation ID, IP, user agent. No update or delete path exists. See [ADR-0007](../adr/0007-append-only-audit-log.md). | Commit 013 |
| **Event Publisher** | Writes integration events through a transactional outbox, so an event is never published for a transaction that rolled back. | Commit 020 |
| **Provenance Service** | Assembles the source → version → evidence → approval chain for any asset. | Commit 011 |

---

## Persistence

Repositories are the only component that talks to the database. They accept
the scope predicate from the PDP and apply it to every query returning
governed rows. Domain services never construct raw queries, which is what keeps
the authorisation guarantee from depending on reviewer vigilance.

Alembic owns schema change. Migrations are reviewed like code and are part of
the pre-commit validation gate from Commit 003 onward.

---

## What is deliberately not here yet

The AI and agent components (model registry, prompt registry, tool registry,
agent orchestrator, evaluation) are **not** shown at this level. They are
introduced in Commits 026–028 and their internal decomposition depends on
requirements not yet available. Sketching them now would put shapes in a
diagram that later code would have to either honour or quietly contradict.

> Recorded as **A-06** in [assumptions.md](assumptions.md).

---

**Previous:** [C4 Level 2 — Containers](containers.md) · **Next:** [Domain boundaries](domain-boundaries.md)
