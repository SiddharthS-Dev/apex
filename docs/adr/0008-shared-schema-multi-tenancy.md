# ADR-0008 — Shared-schema multi-tenancy with enforced isolation

- **Status:** Accepted
- **Date:** 2026-09-15
- **Supersedes assumption:** A-07

## Context

APEX is multi-tenant: `Tenant` is one of the ABAC attributes the platform
evaluates. How tenants are isolated physically was left open through Commits
001–002 and tracked as assumption **A-07**, because the choice constrains every
migration and cannot be changed cheaply once domain tables exist.

Three models were available:

| Model | Isolation | Cost |
| --- | --- | --- |
| Database per tenant | Strongest | An operational unit per tenant: connections, migrations, backups, monitoring |
| Schema per tenant | Strong | Migrations fan out across schemas; connection routing per request |
| Shared schema + discriminator | Logical | One migration path; isolation becomes a correctness property of the code |

The shared-schema model's weakness is specific and well known: isolation
depends on every query filtering correctly. A single `SELECT` that forgets
`WHERE tenant_id = ...` is a cross-tenant data leak that no test of that query's
own behaviour would catch. That risk is the whole argument for the other two
models.

It is also, however, a risk that can be engineered away — if the filter is
applied by the data-access layer rather than by the author of each query.

## Decision

APEX uses **logical multi-tenancy**: one PostgreSQL database, one schema, and a
mandatory `tenant_id` discriminator on tenant-owned tables.

Isolation is enforced at the data-access layer, not by convention:

1. **`TenantScoped` mixin** declares a non-nullable, indexed `tenant_id`.
   Declaring the mixin is what opts a model into enforcement; a model without
   it is global by definition, so tenancy is an explicit choice per table.
2. **Reads are filtered by a session event.** A `do_orm_execute` listener
   rewrites every ORM `SELECT` touching a tenant-scoped entity to filter on the
   active tenant. Queries carry no tenant predicate of their own.
3. **Reads without a tenant are refused, not widened.** With no active tenant,
   a tenant-scoped query raises `TenantContextMissingError`. The failure mode
   of forgetting the context is an error, never a silent full-table read.
4. **Writes are stamped and guarded.** A `before_flush` listener stamps new
   rows with the active tenant and raises `CrossTenantAccessError` on any
   attempt to insert, update or delete a row belonging to another tenant. An
   explicitly supplied `tenant_id` cannot override the active scope.
5. **The escape hatch is explicit and greppable.** `system_scope()` suspends
   filtering for genuinely cross-tenant platform work. It is never used to
   serve a user request.

This operates at the same layer as
[ADR-0004](0004-authorization-at-retrieval.md) and by the same mechanism:
unauthorised rows are never loaded, so no later code path can leak them. Tenant
is the first scope dimension; the remaining ABAC attributes layer on in
Commit 005.

**Stronger isolation stays reachable.** Tenancy is a mixin and the filter is a
session-level concern, so moving to schema- or database-per-tenant would change
connection routing and the filter's implementation — not the domain models, and
not any query.

## Consequences

**Positive**

- One migration path, one connection pool, one database to operate and back up.
- Isolation is a property of the data-access layer, so a new query is isolated
  by construction. Forgetting the filter is not an available failure mode.
- Cross-tenant mistakes fail loudly at flush time rather than corrupting data.
- Onboarding a tenant is a row, not an infrastructure operation.

**Negative**

- A defect in the session guards is a platform-wide leak rather than a
  contained one. This is the real cost of the model, and the reason the guards
  carry dedicated tests that query without any predicate of their own
  (`backend/tests/test_tenancy.py`).
- Tenants share physical resources: a noisy tenant affects others, and
  per-tenant restore requires a filtered export rather than a database restore.
- Regulatory regimes demanding physical separation cannot be met by this model.
  That would trigger the migration path above.
- Every tenant-scoped index must lead with or include `tenant_id`, or queries
  degrade as tenant count grows.

**Neutral**

- PostgreSQL row-level security was considered as a second line of defence. Not
  adopted now, for the reason given in ADR-0004: it splits the authorisation
  model across two languages. It remains the natural hardening step if defence
  in depth at the database becomes a requirement, and it composes with this
  decision rather than replacing it.
