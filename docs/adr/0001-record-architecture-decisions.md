# ADR-0001 — Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

APEX is built incrementally over many commits, and the decisions taken early
constrain everything that follows — the persistence model, the authorization
plane, how domains talk to each other. A decision that is only visible in a
diff is effectively invisible: six months later the *what* is readable from the
code but the *why* is gone, and the reasoning gets re-litigated or, worse,
silently violated.

The platform also has explicit governance and provenance requirements of its
own. A system that insists every published claim be traceable to its evidence
should hold its own architecture to the same standard.

## Decision

Architecture decisions are recorded as numbered Markdown files in `docs/adr/`,
following Michael Nygard's format: context, decision, consequences.

- One file per decision, named `NNNN-short-title.md`.
- Numbered sequentially from 0001.
- Status is one of Proposed, Accepted, Deprecated or Superseded.
- A superseded ADR is never deleted or rewritten. It is marked superseded and
  links forward to the record that replaces it.
- An ADR is written when a decision is *load-bearing*: when reversing it later
  would require changing code across more than one bounded context.

## Consequences

**Positive**

- The reasoning behind constraints survives staff turnover.
- Reviewers can challenge a decision against its stated context rather than
  against assumptions.
- The history of the architecture is auditable in the same way asset
  provenance is.

**Negative**

- Writing an ADR costs time at exactly the moment the decision feels obvious.
- ADRs drift from reality if not maintained. Superseding — not editing — is the
  mitigation.

**Neutral**

- Small, local, easily reversed decisions stay out of `docs/adr/` and live in
  code comments where they belong.
