"""Region-aware database routing.

A **seam**, not a multi-region deployment. Exactly one region is configured at
P02, and behaviour is identical to the single-engine arrangement it replaces.
What changes is that the shape now permits a second region without redesigning
the domain model — which was the whole point of doing this before the ontology
lands rather than after.

Two consequences are worth stating explicitly, because they constrain later
work rather than follow from it:

**The tenant registry is control-plane data.** You cannot route a tenant to its
region without first reading which region it is in, so ``tenant``, ``region``
and ``jurisdiction`` must stay globally reachable even once tenant-*owned* data
is regional. At P02 they share the one database. The split exists in the code
so that later it can exist in the deployment.

**Cross-tenant platform queries become per-region iterations.** With regional
data there is one outbox and one audit table *per region*, so a publisher polls
each. P02 does not implement the iteration — there is one region — but nothing
here forecloses it.

Configuration:

- ``APEX_DATABASE_URLS`` — ``region=url,region=url``. Empty (the default) maps
  ``APEX_DEFAULT_REGION`` to ``APEX_DATABASE_URL``, which is today's behaviour.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.database import create_engine_for_url
from app.platform.residency import (
    RegionKind,
    TenantResidency,
    get_residency_resolver,
)


class UnknownRegionError(RuntimeError):
    """No database is configured for a region.

    Raised rather than falling back to the default. For a strict tenant this is
    the failure that keeps a compliance promise honest; for an unrestricted one
    it means a misconfiguration, which is also worth failing on.
    """


def parse_region_map(raw: str) -> dict[str, str]:
    """Parse ``region=value,region=value`` into a mapping.

    Blank entries are ignored. A malformed entry raises rather than being
    skipped: a silently dropped region is a routing bug that surfaces much
    later, somewhere less obvious.
    """
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        region, separator, value = item.partition("=")
        if not separator or not region.strip() or not value.strip():
            raise ValueError(
                f"Malformed region mapping entry {item!r}; expected 'region=value'"
            )
        mapping[region.strip()] = value.strip()
    return mapping


def database_urls(settings: Settings | None = None) -> dict[str, str]:
    """Region to database URL. Falls back to the single-region configuration."""
    settings = settings or get_settings()
    configured = parse_region_map(settings.database_urls)
    if configured:
        return configured
    return {settings.default_region: settings.database_url}


def configured_regions(settings: Settings | None = None) -> frozenset[str]:
    """Regions this deployment can actually reach."""
    return frozenset(database_urls(settings))


#: Keyed by the resolved **URL**, not by region name.
#:
#: Keying by region looked natural and was wrong: two callers naming the same
#: region with different configuration -- a reconfigured deployment, or a test
#: pointing at a different database -- received whichever engine happened to be
#: built first, silently talking to the wrong database. The URL is what an
#: engine actually is, so it is what identifies one.
_engines: dict[str, AsyncEngine] = {}


def resolve_region_url(
    region: str | None = None,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Return ``(region, url)`` for a region, refusing an unconfigured one."""
    settings = settings or get_settings()
    target = region or settings.default_region

    urls = database_urls(settings)
    if target not in urls:
        raise UnknownRegionError(
            f"No database configured for region {target!r}; "
            f"configured regions are {sorted(urls)}"
        )
    return target, urls[target]


def get_engine_for_region(
    region: str | None = None,
    settings: Settings | None = None,
) -> AsyncEngine:
    """Return the engine serving one region, creating it on first use.

    The unconfigured-region check runs on **every** call, not only on a cache
    miss. Otherwise a region that was once configured would keep resolving from
    the cache after it had been removed from configuration.
    """
    _, url = resolve_region_url(region, settings)

    if url not in _engines:
        _engines[url] = create_engine_for_url(url, settings or get_settings())

    return _engines[url]


def get_session_factory_for_region(
    region: str | None = None,
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to one region's engine."""
    return async_sessionmaker(
        bind=get_engine_for_region(region, settings),
        expire_on_commit=False,
        autoflush=False,
    )


async def resolve_region_for_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kind: RegionKind = "relational",
) -> str:
    """Which region holds one placement of a tenant's data.

    Refuses for a strict tenant with that placement unassigned.
    """
    residency: TenantResidency = await get_residency_resolver().resolve(
        session, tenant_id
    )
    return residency.require(kind)


async def session_for_tenant(
    control_session: AsyncSession,
    tenant_id: uuid.UUID,
) -> AsyncIterator[AsyncSession]:
    """Yield a session against the region holding this tenant's relational data.

    ``control_session`` reads the registry; the yielded session is where the
    tenant's own rows live. At P02 they are the same database.
    """
    region = await resolve_region_for_tenant(control_session, tenant_id, "relational")
    factory = get_session_factory_for_region(region)
    async with factory() as regional:
        yield regional


async def dispose_region_engines() -> None:
    """Close every regional pool. For shutdown and tests."""
    for engine in list(_engines.values()):
        await engine.dispose()
    _engines.clear()
