# ADR-0009 — Local-first authentication with an OIDC-ready identity model

- **Status:** Accepted
- **Date:** 2026-09-15
- **Resolves assumption:** A-12 (partially — see Consequences)

## Context

The development plan calls for a "JWT/OIDC-ready structure" without naming a
provider, and no enterprise identity provider is available to integrate against
yet. That left two options: build federated authentication first against a
provider we would have to guess at, or build local authentication now in a way
that does not have to be torn out later.

The failure to avoid is well known: treat "user" and "password holder" as the
same concept, then discover that adding SSO means reshaping the user table, the
role assignments and every authorisation check that reads them.

## Decision

Local email-and-password authentication now; the identity model shaped so that
federation is an addition rather than a replacement.

**Credentials are an attribute of a user, not their definition.**
`User.password_hash` is nullable. A user who authenticates through a provider
simply has no local credential. Adding federation means adding a
`federated_identity` table keyed on `(provider, subject) → user_id`; `User`,
`Role`, `UserRole`, `RolePermission` and every authorisation code path are
untouched.

**Password hashing is Argon2id** (`argon2-cffi`, library defaults) — the
algorithm OWASP recommends for new applications, and memory-hard, so GPU attack
economics are far worse than for bcrypt or PBKDF2. Parameters are encoded in
the hash, and `check_needs_rehash` upgrades them transparently at next login.

**Access tokens are short-lived JWTs carrying `sub` and `tid`.** The tenant
travels in the token, so isolation follows the authenticated principal rather
than anything the caller sends. `iss` and `aud` are verified, so a token minted
for another service cannot be replayed here.

**Permissions are *not* embedded in the token.** They are read per request.
This costs a query and buys immediate revocation: removing a role takes effect
on the next request rather than at token expiry.

**Refresh tokens are opaque, stored hashed, and rotated on use.** Only a
SHA-256 digest is stored, so a database disclosure yields no usable sessions.
Each refresh revokes the token it consumed, which makes a replayed — and
therefore possibly stolen — token fail rather than silently succeed.

**Sessions are first-class rows**, so logout is immediate: the access token
carries `sid`, and every authenticated request checks the session is neither
revoked nor expired.

**401 and 403 are kept distinct.** 401 means *we do not know who you are*; 403
means *we know, and you may not*. Collapsing them leaves a client unable to
tell "log in again" from "request access", and makes the audit trail
misdescribe what happened.

**Authentication lookups use `system_scope()`**, in exactly two places: finding
a user by email at login, and finding a session by refresh-token hash. Both are
genuinely pre-tenant — the credential is what reveals which tenant the
principal belongs to. Both are commented at the call site.

## Consequences

**Positive**

- Usable authentication today, with no provider dependency.
- Adding OIDC is additive: a new table and a new login route.
- Revocation of both roles and sessions is immediate.
- Refresh rotation makes token theft detectable.
- Algorithm-agnostic signing, so moving to asymmetric keys — which federation
  will want, so verifiers need no shared secret — is configuration.

**Negative**

- We now own password storage, reset flows and lockout policy, all of which a
  provider would have handled. Reset and lockout are **not** implemented here
  and are required before any real deployment.
- Checking permissions and session validity per request costs two queries on
  every authenticated call. Both are indexed point lookups, and the
  alternative is stale authorisation.
- `email` is unique platform-wide rather than per tenant (assumption **A-16**),
  so one address cannot hold accounts in two tenants. Revisit if the
  requirements call for it; it is a schema change plus a tenant hint at login.

**Neutral**

- No password complexity policy, rotation rule or MFA. These are policy
  decisions the requirements have not yet specified, and inventing them would
  create a compliance posture nobody approved.
- Account lockout after repeated failures is deliberately absent for the same
  reason — the threshold and lockout duration are policy. Login timing is
  equalised so failures do not enumerate addresses, which is the part that is
  a correctness property rather than a policy choice.
