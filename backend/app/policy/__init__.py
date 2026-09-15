"""Policy bounded context: tenant registry and attribute-based access control.

The context is organised as four separable stages, in dependency order:

===========================  =========================================
:mod:`app.policy.models`     Policy definition -- tenants and rules, as data
:mod:`app.policy.attributes` Attribute resolution -- facts about a request
:mod:`app.policy.engine`     Policy decision -- a pure, deterministic evaluator
:mod:`app.policy.enforcement` Enforcement -- turning a decision into an outcome
===========================  =========================================

**Framework only, by design.** Commit 005 delivers the machinery. The attribute
domains it will carry -- data classification (A-13), audience (A-14), geography
and organisation -- are *not* modelled here, because their semantics are not
yet specified. Nothing in this package assumes classification levels are
ordered, that audiences nest or exclude one another, or that geography means
jurisdiction rather than residency. Each arrives as a registered attribute
resolver, and an ordering-aware comparison arrives as a registered operator;
neither requires changing the evaluator or the schema.
"""
