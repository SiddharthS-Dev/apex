# ADR-0010 — Hybrid tenancy: shared-schema default with per-tenant residency

- **Status:** Accepted
- **Date:** 2026-09-15
- **Supersedes:** [ADR-0008 — Shared-schema multi-tenancy with enforced isolation](0008-shared-schema-multi-tenancy.md)
- **Resolves assumptions:** A-18, A-19 (residency half), A-25
- **Master Prompt reference:** §9, §10

## Context

ADR-0008 chose shared-schema multi-tenancy with a mandatory `tenant_id` and
isolation enforced by session guards. That decision was sound and its mechanism
is retained unchanged. What it got wrong was its framing: it presented
shared-schema as *the* model, and listed regulatory demands for physical
separation under **Negative** consequences as an accepted limitation.

Master Prompt §10 makes that limitation unacceptable. Geography has two distinct
meanings, and the platform must model both:

| | Jurisdiction | Physical residency |
| --- | --- | --- |
| What it is | Legal and business geography governing users, organisations, contracts, policy and regulation | Where relational data, object storage, backups, logs, telemetry and derived artefacts are physically stored and processed |
| Nature | An attribute the PDP evaluates | A deployment and data-architecture concern |
| Can shared schema satisfy it? | Yes | **No** |

§10 states directly: *"Do not claim that shared-schema tenancy alone satisfies
physical data residency"*, and residency *"may override the default
shared-schema assumption for affected tenants."*

Separately, ADR-0008 left the `tenant` table inside the Policy bounded context.
That forced every tenant-owned table to carry `tenant_id` as a bare UUID —
a foreign key from Identity or Audit into Policy would have inverted the context
map — leaving the platform's most fundamental referential rule enforced only by
convention (assumption A-18).

## Decision

**Shared-schema multi-tenancy remains the default. It is no longer the only
model, and the platform no longer claims it satisfies residency.**

**1. Tenant is control-plane data.** `Tenant`, `Region` and `Jurisdiction` move
to a platform context that sits below every bounded context and depends on none.
You cannot route a tenant to its region without first reading which region it is
in, so this data must remain globally reachable even once tenant-*owned* data
becomes regional.

**2. The tenant edge is enforced by the database.** Every tenant-owned table now
carries `tenant_id → tenant.id` with `ON DELETE RESTRICT`. Deleting a tenant
that owns data is refused rather than cascaded: audit records are immutable by
design, and a cascade would defeat that precisely where it matters most.

**3. Jurisdiction and residency are modelled separately.** Jurisdiction is an
ABAC attribute exposed to the PDP as `geography.jurisdiction`. Residency is four
region assignments per tenant — relational, object storage, processing, backup —
plus a mode.

**4. Strict residency fails closed.** A tenant is `unrestricted` (unassigned
placements fall back to the default region) or `strict`. For a strict tenant an
unassigned or unconfigured region is a **refusal**, never a fallback. A residency
model that quietly does the wrong thing when it cannot do the right thing is
worse than no model, because it reports compliance it has not achieved.

**5. Routing is a seam, not a deployment.** Engines are keyed by region and
buckets resolved by region. Exactly one region is configured, and behaviour is
identical to the single-engine arrangement it replaces.

**6. Residency architecture is not residency enforcement.** Holding a tenant's
data in Frankfurt requires a deployment in Frankfurt. This ADR delivers the
abstraction that makes that possible without reshaping the domain model. It does
not deliver the deployment, and the platform must not claim otherwise.

### The isolation invariant, restated precisely

ADR-0008 asserted that cross-tenant access is *not expressible*. Master Prompt
§12 requires a `CROSS_TENANT_APPROVED` audience, so that assertion is replaced
by a more exact one, which the P05 sharing mechanism must satisfy:

> **Tenant isolation is absolute for all queries. Cross-tenant access is not a
> mode the guard can enter; it is a distinct, single-resource, governed,
> time-bounded, audited operation that returns exactly one explicitly-granted
> row, or nothing.**

No cross-tenant grant mechanism is implemented here. This ADR states the
invariant now so that P05 extends it rather than superseding it.

## Consequences

**Positive**

- The platform can describe its residency posture honestly, including saying no.
- A strict tenant either is genuinely compliant or fails loudly. There is no
  third state in which it appears compliant and is not.
- The referential rule the data model always implied is now enforced by
  PostgreSQL rather than by reviewer vigilance.
- Regional isolation becomes a deployment change, not a domain-model redesign —
  which was the point of doing this before the ontology lands.
- Object placement is recorded per row, so a residency change relocates new
  objects without stranding existing ones.

**Negative**

- A second region means a second database, a second outbox and a second audit
  table. Platform queries that today scan one table become per-region
  iterations. The seam permits this; nothing implements it.
- The control plane becomes a dependency of every regional operation, and
  therefore a new availability concern.
- Residency *change* is a physical data migration, not a column update. It is
  not modelled as an effective-dated history (assumption A-34).
- Cross-region joins are unavailable. All governed queries are tenant-scoped, so
  this is not currently a constraint — but it becomes one for any future
  cross-tenant analytics.

**Neutral**

- The isolation mechanism from ADR-0008 — mandatory discriminator, default-deny
  session guards, `system_scope()` — is unchanged and remains correct. This ADR
  supersedes that decision's *framing and scope*, not its mechanism.
- PostgreSQL row-level security remains unadopted, for the reason given in
  ADR-0004. It composes with this decision rather than replacing it.
