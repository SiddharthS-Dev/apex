# Architecture decision records

Load-bearing decisions, recorded in
[Michael Nygard's format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
context, decision, consequences.

A decision is recorded here when reversing it later would require changes
across more than one bounded context. Small, local, easily reversed decisions
live in code comments instead.

| ADR | Decision | Status | Superseded by |
| --- | --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted | — |
| [0002](0002-technology-stack.md) | Technology stack | Accepted | — |
| [0003](0003-modular-monolith-with-bounded-contexts.md) | Modular monolith with bounded contexts | Accepted | — |
| [0004](0004-authorization-at-retrieval.md) | Authorisation evaluated at retrieval | Accepted | — |
| [0005](0005-evidence-gated-publication.md) | Evidence-gated publication | **Superseded** | [0011](0011-governance-gate-model.md) |
| [0006](0006-integration-by-reference-and-event.md) | Integration by reference and event | Accepted | — |
| [0007](0007-append-only-audit-log.md) | Append-only audit log | Accepted | — |
| [0008](0008-shared-schema-multi-tenancy.md) | Shared-schema multi-tenancy with enforced isolation | **Superseded** | [0010](0010-hybrid-tenancy-and-data-residency.md) |
| [0009](0009-local-first-authentication.md) | Local-first authentication with an OIDC-ready identity model | Accepted | — |
| [0010](0010-hybrid-tenancy-and-data-residency.md) | Hybrid tenancy: shared-schema default with per-tenant residency | Accepted | — |
| [0011](0011-governance-gate-model.md) | Governance gate model G0–G6 | Accepted | — |

### How supersession is recorded

**Superseded ADR files are never edited.** Master Prompt §43 requires accepted
ADRs to be immutable historical decisions, so the relationship lives in the
table above rather than in a status line added to the old file. Reading a
superseded ADR tells you what was decided at the time; reading this index tells
you what replaced it.

Note that this differs from the practice ADR-0001 describes, which anticipated
marking the old file. The Master Prompt is authoritative, and ADR-0001 is itself
immutable, so the divergence is recorded here rather than corrected there.

---

## How these fit together

ADRs 0003–0007 are not independent. Each of the four platform guarantees below
depends on more than one of them, which is why they are recorded rather than
left to convention.

| Guarantee | Rests on |
| --- | --- |
| A user cannot reach content they are not entitled to, by **any** path | 0004 (retrieval-time filtering), 0010 (tenant filter, default-deny, residency), 0003 (one process, one policy decision point) |
| A published claim is always backed by valid evidence | 0011 (G2 gates G6), 0003 (transition and audit commit together) |
| The authoritative source of any value is answerable | 0006 (no duplication), 0005 (recorded authority per claim) |
| What happened can be reconstructed and trusted | 0007 (immutability), 0003 (audit in the same transaction) |

Removing any one of them weakens a guarantee that the others alone do not
provide — most visibly 0003, which is what makes 0005 and 0007 atomic rather
than best-effort.

---

## Writing a new ADR

1. Copy the structure of an existing record.
2. Number sequentially; name the file `NNNN-short-title.md`.
3. Status is `Proposed`, `Accepted`, `Deprecated` or `Superseded`.
4. State the **context** honestly, including the pull towards the option not
   taken. An ADR that makes its decision sound inevitable is not useful to
   someone later wondering whether to revisit it.
5. List **negative** consequences. A record with only positives has not been
   thought through.
6. Add a row to the table above.

Never edit or delete an accepted ADR to reflect a changed decision. Write a new
one, and mark the old one `Superseded` with a link forward.
