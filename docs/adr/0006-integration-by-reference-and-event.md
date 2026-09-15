# ADR-0006 — Integration by reference and event

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

APEX integrates with five systems that are authoritative for their own records
— IVEOS, Vanguard, CK/ESG-AIoT, Meris and Helix — and the development plan
states directly that APEX must not duplicate authoritative records.

The pull towards duplication is strong and always locally reasonable: a
nightly sync makes joins easy, removes a network dependency from the read path,
and survives the upstream being down. The cost arrives later. Two copies of a
record drift, and at that point the platform cannot answer the only question
that matters about a governed claim — *which one is correct?* For a system
whose product is traceability, a record that might be stale is worse than no
record, because it looks authoritative.

## Decision

APEX integrates **by reference and by event**. It does not replicate upstream
records.

1. **Hold identifiers, not attributes.** APEX stores the upstream system and
   record ID. It does not copy that record's fields into its own tables.
2. **React to events.** Upstream systems publish; APEX subscribes. Events carry
   event ID, correlation ID and causation ID, and are **idempotent by
   contract** — processing one twice must equal processing it once.
3. **Resolve on demand** where current upstream attributes are genuinely
   needed for display, with explicit failure handling when the upstream is
   unavailable. A resolved value is never written back into an APEX table as if
   it were owned.
4. **Cache only with visible staleness.** Where a cache is unavoidable for
   performance, its age is carried through to the UI. A cached value never
   presents as authoritative.
5. **Adapters are boundaries.** Each external system has one adapter module
   translating its vocabulary into APEX's. No upstream schema leaks past it.
6. **Unprocessable events dead-letter.** They are never silently dropped;
   an unprocessed event is a visible operational fact.

## Consequences

**Positive**

- No divergence is possible, because there is no second copy to diverge.
- The authoritative source of any value is answerable by construction.
- Upstream schema changes are absorbed in one adapter, not across the codebase.
- Idempotency makes replay and recovery safe — the ordinary tool for an
  integration incident.

**Negative**

- Joins across an authority boundary are not available in SQL. Queries needing
  upstream attributes must resolve them, which is slower and can fail.
- APEX availability becomes partly coupled to upstream availability for
  resolve-on-demand paths. Degradation must be designed per view.
- Event-driven state is eventually consistent. APEX may briefly hold a stale
  derived value; that window must be acceptable to the governance model for
  each case.

**Neutral**

- The transport is not decided here. The contract is defined in Commit 020 such
  that the transport can change without touching producers or consumers.
- **Assumption A-03**: what each of the five systems is authoritative *for* is
  not yet specified, so no adapter contracts are written. The pattern is safe
  to commit to; the contracts are not yet knowable. See
  [assumptions.md](../architecture/assumptions.md).
