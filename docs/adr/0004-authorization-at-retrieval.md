# ADR-0004 — Authorisation evaluated at retrieval

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

APEX exposes the same governed content through several very different read
paths: direct API access, enterprise search, hybrid vector retrieval,
authorisation-aware graph traversal, and AI context assembly.

The conventional approach — check permissions in the endpoint, then query —
breaks down here. Each new read path is a new place to remember the check, and
the paths are not equally obvious:

- A graph traversal can arrive at a node the caller could not open directly.
- A vector search can rank a chunk from a restricted document into the top
  results.
- An agent can assemble context from documents the caller never named.

In each case the leak happens through a path nobody thought to guard, not
through a failed check. Filtering *after* retrieval also means the ranking was
computed over rows the caller cannot see, so result counts, relevance scores
and pagination all quietly describe a corpus that is not theirs.

## Decision

Authorisation is evaluated **at retrieval**, inside the query, on every path.

The Policy Decision Point does not return a boolean. It returns a **scope
predicate** — the set of conditions describing which rows this principal may
see, derived from RBAC capability and ABAC attributes (tenant, organisation,
audience, geography, classification).

- The repository layer applies that predicate to every query returning governed
  rows.
- Domain services never construct raw queries.
- Search, graph traversal and AI retrieval use the same repository layer, so
  they inherit the same filter.
- Unauthorised rows are **never loaded**, so no later code path can leak them.

This is why the ADR-0002 choice of a single PostgreSQL store for relational
data, full-text search and vectors matters: it keeps retrieval and permission
filtering in one query plane. With a separate vector database, the predicate
cannot participate in the ranking query and filtering necessarily moves back
into application code.

## Consequences

**Positive**

- A new read path is authorised by construction. Forgetting the check is not
  an available failure mode.
- Result counts, relevance ranking and pagination are computed over the
  caller's visible corpus, so they are honest.
- Agents cannot exceed their caller: they run under the caller's principal and
  hit the same predicate.
- The same mechanism produces the audit trail — the predicate that filtered a
  query describes the access that occurred.

**Negative**

- Every governed query costs a predicate. Indexes must cover the ABAC
  attributes or the filter dominates query plans.
- Writing raw SQL for a governed table becomes a reviewable exception, not a
  convenience.
- The PDP is on the hot path of every read, so it must be cheap and cacheable
  per request.

**Neutral**

- Row-level security in PostgreSQL was considered as an alternative enforcement
  point. It was not chosen because the ABAC attributes needed for a decision
  are application concepts, and expressing them as database policies splits the
  authorisation model across two languages. Revisit if defence in depth at the
  database layer becomes a requirement.
