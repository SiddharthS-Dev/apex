# ADR-0005 — Evidence-gated publication

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

APEX exists so that published claims are traceable to what backs them. The
failure mode it is built to prevent is a specific and familiar one: an asset is
approved while its supporting evidence is provisional, the evidence later
weakens or expires, and the claim stays published — now unsupported, and
indistinguishable from a claim that is still sound.

Treating evidence as metadata makes this inevitable. Metadata can be absent,
stale or contradicted without anything happening. The only way an expiry has
teeth is if the publication decision is *derived from* evidence state rather
than recorded alongside it.

## Decision

Evidence is a **precondition of publication**, enforced by the state machine.

Every claim carries four elements. Absent any one, the claim is incomplete:

| Element | Question |
| --- | --- |
| Authority | Who is entitled to assert this? |
| Evidence reference | What backs it? |
| Confidence | How strongly? |
| Validity window | Between which dates is this true? |

Rules:

1. **Gate G2 (claim verification) cannot pass** while any claim on the asset is
   unevidenced or outside its validity window.
2. **Gate G4 (publication) requires G2**, so no asset reaches an audience with
   unsupported claims.
3. **Expiry is evaluated, not remembered.** When a claim's validity window
   closes, the asset becomes eligible for G6 (retention) and
   `asset.expiry.due` fires. No human has to notice.
4. **Evidence is versioned with the asset.** Superseding evidence does not
   retroactively alter what a past version claimed — that history is what
   provenance reconstructs.
5. **Confidence is recorded, never inferred.** The system does not compute a
   confidence it was not given.

## Consequences

**Positive**

- An unsupported claim cannot be published through the normal path. This is a
  structural property, not a review outcome.
- Expiry is mechanical, so "still published, no longer true" has a bounded
  lifetime.
- Provenance can answer *what backed this claim, on the day it was published* —
  the question that matters in an audit.
- Retrieval can rank on evidence confidence and freshness because both are
  first-class data.

**Negative**

- Publishing costs more up front. Stewards must supply evidence before G2, not
  after, and some genuinely useful content will sit at G1 longer than its
  author would like.
- Validity windows must be maintained. An asset with many claims has many
  expiry horizons, and the review load is real.
- Bulk-importing legacy content that predates the evidence model needs an
  explicit path. That path must mark such assets distinctly rather than
  fabricating evidence for them — to be designed when the migration
  requirement is specified.

**Neutral**

- Nothing here prevents drafting, circulating or reviewing unevidenced
  content. The constraint binds at publication, not at creation.
