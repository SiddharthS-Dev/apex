# ADR-0011 — Governance gate model G0–G6

- **Status:** Accepted
- **Date:** 2026-09-15
- **Supersedes:** [ADR-0005 — Evidence-gated publication](0005-evidence-gated-publication.md)
- **Resolves assumption:** A-04 (structurally)
- **Master Prompt reference:** §16, §17

## Context

ADR-0005 established that evidence is a precondition of publication — a
principle this record retains in full. It stated that principle against a gate
sequence taken from the pre-specification development strategy, in which
**G2 was claim verification** and **G4 was publication**.

Master Prompt §16 names a different sequence:

| Gate | Master Prompt §16 | ADR-0005's assumption |
| --- | --- | --- |
| G0 | Intake | Inventory |
| G1 | Validation | Technical Verification |
| G2 | **Evidence Review** | Claim Verification |
| G3 | Business Review | Security and Rights |
| G4 | **Risk & Compliance Review** | **Publication** |
| G5 | Executive Approval | Performance Review |
| G6 | **Publication / Operational Release** | Retention |

The overlap is partial and the divergence is load-bearing: ADR-0005 said *"G4
(publication) requires G2"*. Under the specification, publication is G6 and G4
is risk and compliance. Code written against ADR-0005 would attach the evidence
precondition to the wrong gate — the platform would appear to gate publication
on evidence while actually gating a risk review on it.

No gate code exists yet, so the cost of correcting this now is a document.

## Decision

**The seven gates are as Master Prompt §16 defines them**, and ADR-0005's
principle is restated against them.

| Gate | Decision it records |
| --- | --- |
| **G0 Intake** | Is this sufficiently defined to enter the governed lifecycle? |
| **G1 Validation** | Is it structurally and factually valid? |
| **G2 Evidence Review** | Do material claims have adequate evidence and provenance? |
| **G3 Business Review** | Is it business-relevant, owned, and commercially or operationally justified? |
| **G4 Risk & Compliance Review** | Are security, compliance, regulatory and operational risks acceptable? |
| **G5 Executive Approval** | Does the accountable decision-maker approve? |
| **G6 Publication / Operational Release** | May this become operationally or publicly available, per its audience? |

**Evidence gates publication, restated correctly:**

1. **G2 cannot pass** while any material claim is unevidenced or outside its
   validity window.
2. **G6 requires G2**, so nothing reaches an audience with unsupported claims.
3. **Expiry is evaluated, not remembered.** When a claim's validity window
   closes, the artefact becomes eligible for review and an expiry event fires.
   No human has to notice.
4. **Evidence is versioned with the artefact.** Superseding evidence does not
   retroactively alter what a past version claimed.
5. **Confidence is recorded, never inferred.**

**Gate authority is configuration, not code** (§17). The authority model
supports role, permission, organisation, ownership, delegated authority,
escalation and SLA. This is what resolves A-04: the answer to "which role
approves G3?" is *that is tenant configuration*, not a constant in a source
file.

**Separation of duties is a hard rule.** A user cannot approve a gate merely
because they created the underlying record. This is enforced in code, not left
to policy configuration, because it is the one authority rule that is unsafe to
let a tenant switch off by accident.

**No gate is implemented by this ADR.** Gates arrive at P06. This record exists
so that P06 is built against the specification rather than against ADR-0005's
superseded sequence — and so the correction is visible in the decision history
rather than silently applied.

## Consequences

**Positive**

- Gate semantics now match the authoritative specification.
- The evidence-to-publication guarantee attaches to the correct gates.
- Authority becomes configurable, so the platform serves organisations with
  different approval structures without a code change.
- The correction is recorded rather than quietly made, which is the behaviour
  the platform demands of its own users.

**Negative**

- Architecture documentation written against the old sequence must be corrected
  in the same commit, or two descriptions of the gates coexist.
- Configurable authority is more work than constants, and a misconfigured
  authority matrix is a governance failure the platform cannot detect for the
  tenant.

**Neutral**

- ADR-0005 is **not edited**. Its decision stands as the historical record of
  what was decided when evidence-gated publication was first adopted; this
  record supersedes it. The relationship is recorded in the ADR index.
- SLA durations, escalation timeouts and delegation rules remain unspecified as
  *values*. They are configuration, so their absence blocks no structure.
