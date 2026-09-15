# Architecture assumptions register

APEX is being built ahead of its full specification. Where a decision could not
wait, an assumption was made, recorded here, and referenced from the document
that relies on it.

**Purpose of this register:** when the full specification arrives, every entry
below is either *confirmed* (no change) or *corrected* (with the listed impact).
Nothing inferred is left implicit in a diagram or buried in code.

A confirmed assumption that constrains the codebase graduates to an ADR — the
register records that it was once open; the ADR records the decision and its
consequences. A-07 is the first to have made that journey
([ADR-0008](../adr/0008-shared-schema-multi-tenancy.md)).

**How to reconcile:** work the table top to bottom. For each row, mark the
status, and where the specification contradicts the assumption, follow the
*Impact if wrong* column to the artefacts that must change.

| Status | Meaning |
| --- | --- |
| 🟡 Open | Assumed; awaiting specification |
| 🟢 Confirmed | Specification agrees; no change needed |
| 🔴 Corrected | Specification disagrees; impact applied |

---

## Register

| ID | Assumption | Status | Blocks | Impact if wrong |
| --- | --- | --- | --- | --- |
| **A-01** | Technology stack — FastAPI, React 19, Vite chosen to fit the plan's named technologies (PostgreSQL, SQLAlchemy, Alembic, pgvector, S3, JWT/OIDC) | 🟡 Open | — | Low. Recorded in [ADR-0002](../adr/0002-technology-stack.md). Prescribed components are unaffected; only the web framework and frontend stack would change. |
| **A-02** | Ontology entity list is complete as given; attributes, cardinalities and relationship semantics unspecified | 🟡 Open | Commit 006 | **High.** Ontology is referenced by Assets, Graph and Search. A different entity model reshapes three contexts and their migrations. No schema has been asserted for this reason. |
| **A-03** | IVEOS, Vanguard, CK/ESG-AIoT, Meris and Helix are authoritative for their own operational records; APEX integrates by reference and event | 🟡 Open | Commit 021 | Medium. The *integration pattern* is stated explicitly in the plan and is safe. What each system is authoritative *for* is not, so no adapter contracts are written. |
| **A-04** | Gate names and sequence G0–G6 are as listed; each has a single abstract decision owner | 🟡 Open | Commit 012 | **High.** Role mapping, SLA durations, escalation paths and delegation rules are entirely unspecified. The state machine can be modelled; the authorisation matrix cannot. |
| **A-05** | Production environment, orchestrator, regions and tenancy isolation model unspecified | 🟡 Open | Deployment | Low now. All containers are stateless apart from the two data stores. Rises to High if physical tenant isolation is required — see A-07. |
| **A-06** | AI and agent internal decomposition deferred rather than sketched | 🟡 Open | Commits 026–028 | Low. Deliberately absent from [components.md](components.md) rather than guessed. |
| **A-07** | Multi-tenancy is logical — a `tenant` attribute in ABAC, shared schema, row-level filtering | 🟢 **Confirmed** | *(resolved)* | Approved 2026-09-15. Promoted to [ADR-0008](../adr/0008-shared-schema-multi-tenancy.md) and implemented in Commit 003: mandatory `tenant_id`, default-deny session guards, `system_scope()` escape hatch. Stronger isolation remains reachable without touching domain models. |
| **A-08** | Trends T01–T12 exist and are scored on impact, confidence, urgency and readiness | 🟡 Open | Commit 024 | Medium, contained. The twelve trends are **not named** in the plan. The scoring model is stated; the content is not. No trend is invented. |
| **A-09** | The ten APEX brands are as named (Vault, Showcase, Academy, Playbooks, Industry, Partners, Executive, Studio, Exchange, AI) | 🟡 Open | Commit 029+ | Medium. Names are given in the plan; their scope, features and audiences are not. No module structure assumed. |
| **A-10** | KPI definitions carry a formula, target, calculation timestamp and source references | 🟡 Open | Commits 022–023 | Medium. The *shape* is from the plan's deliverables. No actual formula or target is specified, so none is invented — the plan explicitly forbids fabricated production metrics. |
| **A-11** | Retention and expiry are driven by evidence validity windows plus a retention policy | 🟡 Open | Commit 012 (G6) | Medium. The mechanism follows from the evidence model. Concrete retention periods, legal-hold behaviour and archival destination are unspecified. |
| **A-12** | Authentication is OIDC against an enterprise IdP, with JWT bearer tokens for API calls | 🟡 Open | Commit 004 | Medium. The plan says "JWT/OIDC-ready structure", which is what has been assumed. The specific provider, claim mapping and group-to-role sync are unknown. |
| **A-13** | Data classification is an ordered scheme used as an ABAC attribute | 🟡 Open | Commit 005 | Medium. The *levels* are unspecified. An unordered or multi-dimensional scheme would change policy evaluation from a comparison to a lattice. |
| **A-14** | "Audience" is a first-class ABAC attribute governing publication scope (G4) | 🟡 Open | Commits 005, 012 | Medium. Whether audiences nest, overlap, or are mutually exclusive is unspecified and materially affects publication rules. |
| **A-15** | Windows development needs asyncio's deprecated event-loop *policy* API, because psycopg's async driver cannot run on the default `ProactorEventLoop` | 🟡 Open | — | Low, but time-boxed. The policy API is slated for removal in **Python 3.16**. `app/core/runtime.py` degrades to a no-op rather than raising, so the failure would be a clear connection error on Windows only. Fixing it then means selecting the loop at the server entry point, or moving to a driver that supports the proactor loop. Linux, macOS and all containers are unaffected. |

---

## Assumptions deliberately **not** made

Recording what was left blank matters as much as recording what was inferred.
None of the following has been guessed, and no diagram, schema or code implies
a value for them:

- The identities of trends **T01–T12**
- The scope, features or audience of any of the **ten APEX brands**
- Any **KPI formula or target value**
- Any **SLA duration**, escalation timeout or retention period
- Any **ontology attribute or cardinality**
- Any **external system interface contract**
- The **organisational role** behind any gate authority
- The levels of the **data classification** scheme

Where the specification is silent, the artefacts are silent. This is why
[components.md](components.md) omits the AI decomposition and
[domain-boundaries.md](domain-boundaries.md) lists ontology entities without
asserting a schema.

---

## Highest-risk entries

Three entries would force rework across more than one context if wrong. They
should be reconciled first:

1. **A-02 — ontology entity model.** Referenced by Assets, Graph and Search.
2. **A-04 — gate authority matrix.** Determines the governance authorisation
   model, not just its state machine.
3. **A-13 / A-14 — classification and audience semantics.** Together they
   define what ABAC actually evaluates.

All three are needed by **Commits 005–007**, which is the point at which
building further without the specification stops being safe.

> **A-07 (tenancy) was the fourth, and is now resolved.** It was confirmed on
> 2026-09-15 and implemented in Commit 003 before any domain table existed —
> which was the point of settling it early, since retrofitting a discriminator
> across assets, versions and evidence would have been far more expensive.

---

**Previous:** [Authority boundaries](authority-boundaries.md) · **Back to** [Architecture index](README.md)
