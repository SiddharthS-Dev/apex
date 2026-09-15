"""Storage: the object storage boundary.

Infrastructure, not a domain context. It stores bytes, records where they live
and what they hash to, and hands out short-lived read URLs. It deliberately
knows nothing about what an object *means* -- no title, classification,
lifecycle or ownership -- because that is the Asset model, which needs the
ontology (A-02) and does not exist yet.

Two properties are worth knowing before reading the code:

**A caller never supplies a key.** An upload returns a ``StoredObject`` id;
every later operation names that id, the row is read under tenant scope, and
the key comes from the row. Cross-tenant and arbitrary-path access are
therefore not expressible, rather than forbidden by a check someone could
forget.

**Location resolution is a seam, not a constant.**
:mod:`app.storage.locations` decides which bucket a tenant's objects live in.
Today every tenant shares one. If data residency (A-19) turns out to be a
requirement, that resolver is what changes -- and nothing else in this package.
"""
