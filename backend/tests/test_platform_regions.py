"""Region routing seam. Mostly pure — no database required.

P02 delivers routing *architecture* with exactly one region configured. These
tests pin the seam's behaviour so a second region can be added later without
rediscovering what the first one assumed.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.platform import regions
from app.platform.regions import UnknownRegionError, parse_region_map
from app.storage.errors import StorageConfigurationError
from app.storage.locations import RegionAwareBucketResolver, parse_bucket_map

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")


# --- Region map parsing --------------------------------------------------


def test_empty_map_parses_to_nothing() -> None:
    assert parse_region_map("") == {}
    assert parse_region_map("   ") == {}


def test_single_entry_parses() -> None:
    assert parse_region_map("local=postgresql://x") == {"local": "postgresql://x"}


def test_multiple_entries_parse() -> None:
    parsed = parse_region_map("local=a, eu-west-1=b")

    assert parsed == {"local": "a", "eu-west-1": "b"}


def test_malformed_entry_raises_rather_than_being_skipped() -> None:
    """A silently dropped region is a routing bug that surfaces much later."""
    for bad in ("local", "=value", "local=", "  =  "):
        with pytest.raises(ValueError, match="Malformed region mapping"):
            parse_region_map(bad)


# --- Database URL resolution ---------------------------------------------


def test_single_region_falls_back_to_the_plain_database_url() -> None:
    """P02 behaviour: one region, configured exactly as before."""
    settings = Settings(
        default_region="local",
        database_url="postgresql+psycopg://u:p@h/db",
        database_urls="",
    )

    assert regions.database_urls(settings) == {"local": "postgresql+psycopg://u:p@h/db"}


def test_explicit_map_takes_precedence() -> None:
    settings = Settings(
        default_region="local",
        database_url="postgresql+psycopg://ignored/db",
        database_urls="local=postgresql+psycopg://a/db, eu=postgresql+psycopg://b/db",
    )

    assert set(regions.configured_regions(settings)) == {"local", "eu"}


def test_unknown_region_is_rejected() -> None:
    """No fallback: for a strict tenant this keeps a compliance promise honest."""
    settings = Settings(default_region="local", database_urls="local=postgresql+psycopg://a/db")

    with pytest.raises(UnknownRegionError, match="atlantis-1"):
        regions.get_engine_for_region("atlantis-1", settings)


def test_engine_is_reused_for_the_same_database() -> None:
    settings = Settings(
        default_region="local", database_urls="local=postgresql+psycopg://u:p@h/db"
    )

    first = regions.get_engine_for_region("local", settings)
    second = regions.get_engine_for_region("local", settings)

    assert first is second


def test_the_same_region_name_on_a_different_database_gets_a_different_engine() -> None:
    """Regression. The cache was keyed by region name, so the second caller
    received the first caller's engine and silently used the wrong database."""
    one = Settings(default_region="local", database_urls="local=postgresql+psycopg://a/one")
    two = Settings(default_region="local", database_urls="local=postgresql+psycopg://b/two")

    first = regions.get_engine_for_region("local", one)
    second = regions.get_engine_for_region("local", two)

    assert str(first.url) != str(second.url)
    assert "one" in str(first.url)
    assert "two" in str(second.url)


def test_a_region_removed_from_configuration_stops_resolving() -> None:
    """Regression. The unconfigured-region check only ran on a cache miss, so a
    region kept resolving after it had been removed from configuration."""
    configured = Settings(
        default_region="local", database_urls="local=postgresql+psycopg://c/three"
    )
    regions.get_engine_for_region("local", configured)

    removed = Settings(
        default_region="other", database_urls="other=postgresql+psycopg://d/four"
    )
    with pytest.raises(UnknownRegionError, match="local"):
        regions.get_engine_for_region("local", removed)


def test_resolve_region_url_reports_both_parts() -> None:
    settings = Settings(
        default_region="local", database_urls="local=postgresql+psycopg://e/five"
    )

    region, url = regions.resolve_region_url(None, settings)

    assert region == "local"
    assert url == "postgresql+psycopg://e/five"


# --- Bucket map parsing --------------------------------------------------


def test_bucket_map_parses() -> None:
    assert parse_bucket_map("local=a, eu=b") == {"local": "a", "eu": "b"}


def test_malformed_bucket_entry_raises() -> None:
    """A silently dropped region would place bytes somewhere unintended."""
    with pytest.raises(ValueError, match="Malformed bucket mapping"):
        parse_bucket_map("local")


def test_single_bucket_configuration_still_works() -> None:
    """Existing single-bucket deployments are unchanged by P02."""
    resolver = RegionAwareBucketResolver(
        Settings(default_region="local", s3_bucket="apex-objects", s3_buckets="")
    )

    location = resolver.resolve(TENANT)

    assert location.bucket == "apex-objects"
    assert location.placement_region == "local"


def test_region_selects_its_bucket() -> None:
    resolver = RegionAwareBucketResolver(
        Settings(
            default_region="local",
            s3_buckets="local=apex-local, eu-west-1=apex-eu",
        )
    )

    assert resolver.resolve(TENANT, region="eu-west-1").bucket == "apex-eu"
    assert resolver.resolve(TENANT, region="local").bucket == "apex-local"


def test_unconfigured_region_has_no_bucket() -> None:
    resolver = RegionAwareBucketResolver(
        Settings(default_region="local", s3_buckets="local=apex-local")
    )

    with pytest.raises(StorageConfigurationError, match="atlantis-1"):
        resolver.resolve(TENANT, region="atlantis-1")


def test_placement_region_is_carried_on_the_location() -> None:
    """Recorded per object, so placement is a fact rather than an inference."""
    resolver = RegionAwareBucketResolver(
        Settings(default_region="local", s3_buckets="eu-west-1=apex-eu")
    )

    assert resolver.resolve(TENANT, region="eu-west-1").placement_region == "eu-west-1"
