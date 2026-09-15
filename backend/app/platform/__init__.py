"""Platform: the control-plane context.

Sits **below** every bounded context. Identity, Policy, Ontology, Audit, Storage
and Events may all depend on it; it depends on none of them. That position is
what lets tenant-owned tables carry a foreign key to ``tenant`` without
inverting the context map (assumption A-18, now resolved).

Owns three global tables:

``tenant``
    The tenant registry. **Control-plane data**: you cannot route a tenant to
    its region without first reading which region it is in, so this must stay
    globally reachable even when tenant-*owned* data becomes regional. See
    ADR-0010.

``region``
    Physical locations data may live in. A deployment concern.

``jurisdiction``
    Legal and business geography. An *attribute*, consumed by the PDP.

**Jurisdiction and residency are different things** and are modelled
separately, per Master Prompt §10. Jurisdiction governs which rules apply to a
tenant; residency governs where its bytes physically sit. Conflating them is
how a platform ends up claiming compliance it cannot demonstrate.

This context is the registry. It is **not** the isolation mechanism --
:mod:`app.core.tenancy` remains solely responsible for enforcing that a query
sees one tenant's rows. Keeping them apart means a change to how tenants are
administered can never accidentally loosen how they are isolated.
"""
