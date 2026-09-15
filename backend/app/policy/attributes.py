"""Attribute resolution -- stage two of the pipeline.

    Policy Definition -> **Attribute Resolution** -> Policy Decision -> Enforcement

Resolvers turn an :class:`~app.policy.context.AccessRequest` into facts. Each
owns a namespace and may only write keys under it, so two resolvers can never
silently contest the same attribute.

Only two resolvers ship here, and both are *structural* -- they describe the
request itself, not the business. The attributes that A-13, A-14, geography and
organisation will need are added by registering further resolvers; no change to
this module, the engine, or the schema is required.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.policy.context import AccessRequest, AttributeBag, AttributeValue

#: Namespace separator in an attribute key, e.g. ``subject.id``.
SEPARATOR = "."


@runtime_checkable
class AttributeResolver(Protocol):
    """Contributes attributes under a single namespace."""

    @property
    def namespace(self) -> str:
        """The prefix every key this resolver produces must carry."""
        ...

    async def resolve(
        self,
        request: AccessRequest,
        session: AsyncSession,
    ) -> Mapping[str, AttributeValue]:
        """Return attribute names (without namespace) and their values."""
        ...


class RequestAttributeResolver:
    """Facts about the request itself."""

    namespace = "request"

    async def resolve(
        self,
        request: AccessRequest,
        session: AsyncSession,
    ) -> Mapping[str, AttributeValue]:
        values: dict[str, AttributeValue] = {
            "action": request.action,
            "resource_type": request.resource_type,
        }
        if request.resource_id is not None:
            values["resource_id"] = str(request.resource_id)
        return values


class SubjectAttributeResolver:
    """Facts about who is asking.

    Deliberately minimal: identifier and tenant. Roles, permissions,
    organisation and geography are resolved by whoever registers a resolver for
    them -- none of which this module needs to know about.
    """

    namespace = "subject"

    async def resolve(
        self,
        request: AccessRequest,
        session: AsyncSession,
    ) -> Mapping[str, AttributeValue]:
        values: dict[str, AttributeValue] = {"tenant_id": str(request.tenant_id)}
        if request.subject_id is not None:
            values["id"] = str(request.subject_id)
        return values


class DuplicateNamespaceError(ValueError):
    """Two resolvers claimed the same namespace."""


class AttributeRegistry:
    """The set of resolvers used to build an :class:`AttributeBag`."""

    def __init__(self, resolvers: list[AttributeResolver] | None = None) -> None:
        self._resolvers: dict[str, AttributeResolver] = {}
        for resolver in resolvers or []:
            self.register(resolver)

    def register(self, resolver: AttributeResolver, *, replace: bool = False) -> None:
        """Add a resolver, refusing a namespace collision."""
        namespace = resolver.namespace
        if not namespace:
            raise ValueError("A resolver must declare a non-empty namespace")
        if SEPARATOR in namespace:
            raise ValueError(f"Namespace {namespace!r} must not contain {SEPARATOR!r}")
        if not replace and namespace in self._resolvers:
            raise DuplicateNamespaceError(
                f"Namespace {namespace!r} is already registered"
            )
        self._resolvers[namespace] = resolver

    @property
    def namespaces(self) -> frozenset[str]:
        return frozenset(self._resolvers)

    async def resolve(
        self,
        request: AccessRequest,
        session: AsyncSession,
    ) -> AttributeBag:
        """Run every resolver and collect the results into one bag.

        A resolver returning a key already qualified with another namespace is
        a programming error, not something to paper over -- it would let one
        resolver forge another's facts.
        """
        values: dict[str, AttributeValue] = {}
        for namespace, resolver in self._resolvers.items():
            produced = await resolver.resolve(request, session)
            for name, value in produced.items():
                if SEPARATOR in name:
                    raise ValueError(
                        f"Resolver {namespace!r} returned a qualified key {name!r}; "
                        "resolvers return bare names and are namespaced for them."
                    )
                values[f"{namespace}{SEPARATOR}{name}"] = value
        return AttributeBag(values)


def default_registry() -> AttributeRegistry:
    """The structural resolvers, with no business attributes."""
    return AttributeRegistry([RequestAttributeResolver(), SubjectAttributeResolver()])


def access_request(
    *,
    tenant_id: uuid.UUID,
    action: str,
    resource_type: str,
    subject_id: uuid.UUID | None = None,
    resource_id: uuid.UUID | None = None,
    hints: Mapping[str, str] | None = None,
) -> AccessRequest:
    """Build an access request -- a keyword-only constructor for readability."""
    return AccessRequest(
        tenant_id=tenant_id,
        subject_id=subject_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        hints=dict(hints or {}),
    )
