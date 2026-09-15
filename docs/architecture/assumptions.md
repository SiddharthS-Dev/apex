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
| **A-04** | Gate names and sequence G0–G6 are as listed; each has a single abstract decision owner | 🟡 Open | Commit 012 | **High — now the top open risk.** Role mapping, SLA durations, escalation paths and delegation rules are entirely unspecified. Commit 004 built the RBAC *mechanism* and seeded exactly one role, `tenant_admin`, flagged `is_provisional` and described as replaceable. No business role, gate authority or approval right has been invented. |
| **A-05** | Production environment, orchestrator, regions and tenancy isolation model unspecified | 🟡 Open | Deployment | Low now. All containers are stateless apart from the two data stores. Rises to High if physical tenant isolation is required — see A-07. |
| **A-06** | AI and agent internal decomposition deferred rather than sketched | 🟡 Open | Commits 026–028 | Low. Deliberately absent from [components.md](components.md) rather than guessed. |
| **A-07** | Multi-tenancy is logical — a `tenant` attribute in ABAC, shared schema, row-level filtering | 🟢 **Confirmed** | *(resolved)* | Approved 2026-09-15. Promoted to [ADR-0008](../adr/0008-shared-schema-multi-tenancy.md) and implemented in Commit 003: mandatory `tenant_id`, default-deny session guards, `system_scope()` escape hatch. Stronger isolation remains reachable without touching domain models. |
| **A-08** | Trends T01–T12 exist and are scored on impact, confidence, urgency and readiness | 🟡 Open | Commit 024 | Medium, contained. The twelve trends are **not named** in the plan. The scoring model is stated; the content is not. No trend is invented. |
| **A-09** | The ten APEX brands are as named (Vault, Showcase, Academy, Playbooks, Industry, Partners, Executive, Studio, Exchange, AI) | 🟡 Open | Commit 029+ | Medium. Names are given in the plan; their scope, features and audiences are not. No module structure assumed. |
| **A-10** | KPI definitions carry a formula, target, calculation timestamp and source references | 🟡 Open | Commits 022–023 | Medium. The *shape* is from the plan's deliverables. No actual formula or target is specified, so none is invented — the plan explicitly forbids fabricated production metrics. |
| **A-11** | Retention and expiry are driven by evidence validity windows plus a retention policy | 🟡 Open | Commit 012 (G6) | Medium. The mechanism follows from the evidence model. Concrete retention periods, legal-hold behaviour and archival destination are unspecified. |
| **A-12** | Authentication is OIDC against an enterprise IdP, with JWT bearer tokens for API calls | 🟢 **Resolved for now** | *(Commit 004 done)* | Approved 2026-09-15: local password authentication first, structured so OIDC is additive. See [ADR-0009](../adr/0009-local-first-authentication.md). Still open downstream: the specific provider, claim mapping and group-to-role sync. |
| **A-13** | Data classification semantics — ordered, unordered or multi-dimensional | 🟡 **Open, deliberately undecided** | A later commit | Medium. Commit 005 ships the PDP with **no ordering operator at all**: providing `greater_than`/`dominates` would have encoded an answer. Whichever scheme the requirements specify arrives as a *registered operator* plus a *registered resolver* — no change to the evaluator, the schema or existing policies. Pinned by `test_no_ordering_operator_ships_by_default`. |
| **A-14** | Audience semantics — nesting, overlap or mutual exclusion | 🟡 **Open, deliberately undecided** | Commit 012 | Medium. Commit 005 provides `contains_all` **and** `contains_any` and privileges neither, and binds no attribute to any operator. Whether audience matching is subset, intersection or equality becomes a property of the policies someone writes, not of the code. Pinned by `test_set_operators_make_no_assumption_about_membership_semantics`. |
| **A-15** | Windows development needs asyncio's deprecated event-loop *policy* API, because psycopg's async driver cannot run on the default `ProactorEventLoop` | 🟡 Open | — | Low, but time-boxed. The policy API is slated for removal in **Python 3.16**. `app/core/runtime.py` degrades to a no-op rather than raising, so the failure would be a clear connection error on Windows only. Fixing it then means selecting the loop at the server entry point, or moving to a driver that supports the proactor loop. Linux, macOS and all containers are unaffected. |
| **A-16** | A user's email is unique **platform-wide**, not per tenant | 🟡 Open | Commit 004 (implemented) | Medium. Authentication must resolve a principal before any tenant context exists, so the credential has to identify the user on its own. Consequence: one address cannot hold accounts in two tenants. Reversing it means a composite unique key plus a tenant hint at login — a schema change and a login-contract change, but no change to authorisation. |
| **A-17** | Password reset, account lockout, complexity policy and MFA are **not** implemented | 🟡 Open | Before any real deployment | **High for production, zero for development.** These are policy decisions (thresholds, durations, factors) that the requirements do not specify, and inventing them would create a compliance posture nobody approved. Login timing *is* equalised, since resisting account enumeration is a correctness property rather than a policy choice. |
| **A-18** | No foreign key runs from Identity tables to `tenant`; that edge's integrity is enforced in the service layer | 🟡 Open | — | Low. A foreign key would make Identity depend on Policy and invert the context map. Revisit if the requirements justify moving the tenant registry into a foundation context that both depend on — a migration, not a redesign. |
| **A-19** | Geography semantics — jurisdiction, region or data residency | 🟡 **Open, deliberately undecided** | A later commit | **Medium-high.** Residency is not merely a filter: it constrains *where rows may be stored*, which shared-schema tenancy (ADR-0008) may not be able to satisfy. Treated as a pluggable attribute only. If it turns out to mean residency, that interacts directly with A-07 and needs reconciling before it is implemented. |
| **A-20** | Organization structure — flat, hierarchical, or many per tenant | 🟡 **Open, deliberately undecided** | A later commit | Medium. A hierarchy implies inheritance of access down the tree, which is an evaluation rule, not just a data shape. Treated as a pluggable attribute only. |
| **A-21** | Policy combining is deny-overrides, with no other algorithm implemented | 🟡 Open | Commit 012 | Medium. Deny-overrides is a *safety* default, not a business precedence rule. Permit-overrides, first-applicable and explicit priority ordering are **not** implemented, because choosing between them is an authority decision (A-04). The combiner is registrable, so one arrives without touching `evaluate()`. |
| **A-22** | Policy conditions are a flat AND; there is no OR, no nesting, no rule language | 🟡 Open | A later commit | Low-medium. A disjunction is expressed as a second policy. This keeps evaluation total and obviously terminating, and avoids inventing a rule language before the requirements ask for one. Adding nesting later is a schema change plus an evaluator change — contained, but not free. |
| **A-23** | Audit immutability is enforced by a database trigger rather than by role grants | 🟡 Open | Production hardening | Low. ADR-0007 anticipated granting the application role `INSERT`/`SELECT` only. That cannot be done in development, where the app connects as the table owner and an owner can always re-grant. The trigger holds regardless of role, so it is the stronger guarantee; the grant is still worth adding in production as defence in depth. |
| **A-24** | Business event taxonomy for audit is **not** defined | 🟡 Open | Commit 012 | Medium. Every catalogued action is `TECHNICAL` — it names a mechanical event in code that already exists. Gate transitions, approvals, publication and retention events need A-04 to name correctly, and an immutable record is the worst place to put unapproved vocabulary. The writer accepts uncatalogued actions, so no future context is blocked. |

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
- Whether classification levels are **ordered** — no ordering operator exists
- Whether audiences **nest, overlap or exclude** — no operator is privileged
- Whether geography means **jurisdiction or residency**
- Whether organisations are **flat or hierarchical**
- Any **policy precedence rule** beyond the deny-overrides safety default
- The **organisational role** behind any gate authority
- The levels of the **data classification** scheme
- Any **business role** beyond the single provisional `tenant_admin` needed to
  administer a tenant at all
- Any **password policy**: complexity, expiry, lockout threshold or MFA factor

Where the specification is silent, the artefacts are silent. This is why
[components.md](components.md) omits the AI decomposition and
[domain-boundaries.md](domain-boundaries.md) lists ontology entities without
asserting a schema.

---

## ⚠️ A-19: geography may be a tenancy question, not an attribute question

Flagged separately because it is the one open assumption that could invalidate
a decision already made.

Commit 005 treats geography as a pluggable attribute, which is correct **if** it
means jurisdiction — "this content is governed by EU rules" is a fact to filter
on, and the PDP handles it.

If it means **data residency** — "this content must be stored inside the EU" —
it is not an attribute at all. It is a constraint on *where rows physically
live*, and shared-schema logical multi-tenancy
([ADR-0008](../adr/0008-shared-schema-multi-tenancy.md)) cannot satisfy it:
every tenant's rows share one table in one database in one region.

**The tenancy model is not being redesigned now**, and should not be on
speculation. But the impact if residency is what is meant:

| Affected | Impact |
| --- | --- |
| ADR-0008 | Would need superseding, not amending — the isolation model itself changes |
| Every migration | Schema-per-region or database-per-region changes all of them |
| Connection strategy | Routing by region, not one pool |
| `app/core/tenancy.py` | The scope predicate becomes connection selection as well as filtering |
| Object storage | Bucket placement becomes a correctness requirement, not a deployment detail |
| A-05 deployment model | Multi-region becomes mandatory rather than optional |

**Cost of resolving it early versus late:** cheap now — the answer changes a
plan. Expensive after Commits 007–011, when assets, versions, evidence and
provenance all exist and would each need migrating across a new isolation
boundary. This is worth one sentence in the requirements long before it is
worth any code.

---

## What is framework-only, pending the master prompt

Commit 005 delivers the ABAC **machinery**. It deliberately delivers no
attribute *semantics*, because four of them are unspecified. The split:

| Delivered and working | Framework only — awaiting requirements |
| --- | --- |
| Tenant registry, with isolation tests | Data classification (A-13) — no attribute, no ordering operator |
| Policy and condition schema | Audience (A-14) — no attribute, no privileged operator |
| Attribute resolver protocol and registry | Geography (A-19) — no attribute |
| Operator registry, 8 semantics-free operators | Organisation (A-20) — no attribute |
| Deterministic evaluator with explain traces | Precedence beyond deny-overrides (A-21) |
| Deny-overrides combiner, plus a registry for others | Condition nesting / OR (A-22) |
| Enforcement dependency, default-deny | Policy administration API — needs A-04 to know who may write policy |

The test suite pins the *absence* of the undecided semantics as deliberately as
it pins the presence of the machinery: `test_no_ordering_operator_ships_by_default`
and `test_default_registry_carries_only_structural_namespaces` both fail if
someone later guesses.

No HTTP surface was added for policy or tenant administration. Exposing it
requires knowing who is entitled to create a tenant or write a policy, which is
A-04 — so the work stops at the service layer rather than inventing an
authority model.

---

## Highest-risk entries

Three entries would force rework across more than one context if wrong. They
should be reconciled first:

1. **A-02 — ontology entity model.** Referenced by Assets, Graph and Search.
   Blocks Commit 006, which is the next one up.
2. **A-04 — gate authority matrix.** Determines the governance authorisation
   model, not just its state machine. Also gates the policy administration API.
3. **A-19 — geography semantics.** Promoted into this list by Commit 005. If it
   means *data residency* rather than jurisdiction, it constrains where rows may
   physically live, which shared-schema tenancy (ADR-0008) may be unable to
   satisfy. That is a tenancy question wearing an attribute's clothing.
4. **A-13 / A-14 — classification and audience semantics.** Together they define
   what ABAC actually evaluates. Now lower-risk than they were: the PDP is built
   so either answer is additive.

A-02 is needed by **Commit 006**, the next commit in the order. That is now
the point at which building further without the specification stops being safe.

> **A-07 (tenancy) was the fourth, and is now resolved.** It was confirmed on
> 2026-09-15 and implemented in Commit 003 before any domain table existed —
> which was the point of settling it early, since retrofitting a discriminator
> across assets, versions and evidence would have been far more expensive.

---

**Previous:** [Authority boundaries](authority-boundaries.md) · **Back to** [Architecture index](README.md)
