"""Object key generation and validation. No database or storage required.

These are the tests that matter most in this package. A key derived from user
input is a path-traversal and cross-tenant-access primitive, so the property
under test is that caller input reaches the key only as a validated extension.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.storage.errors import InvalidObjectKeyError
from app.storage.keys import (
    MAX_KEY_LENGTH,
    generate_object_key,
    key_belongs_to_tenant,
    safe_extension,
    validate_key,
)

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
OTHER = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")


# --- Extension handling --------------------------------------------------


def test_ordinary_extension_is_kept() -> None:
    assert safe_extension("report.pdf") == ".pdf"


def test_extension_is_lowercased() -> None:
    assert safe_extension("REPORT.PDF") == ".pdf"


def test_missing_extension_yields_nothing() -> None:
    assert safe_extension("report") == ""
    assert safe_extension(None) == ""
    assert safe_extension("") == ""


def test_traversal_in_a_filename_cannot_reach_the_extension() -> None:
    assert safe_extension("../../../etc/passwd") == ""


def test_windows_path_is_reduced_to_its_basename() -> None:
    assert safe_extension(r"C:\Users\ada\report.pdf") == ".pdf"


def test_suspicious_extensions_are_dropped_not_sanitised() -> None:
    """Anything not purely alphanumeric is discarded entirely."""
    for name in ("a.tar.gz/../x", "a.p df", "a.<script>", "a." + "x" * 20):
        assert safe_extension(name) in ("", ".gz")


def test_double_extension_keeps_only_the_last() -> None:
    assert safe_extension("archive.tar.gz") == ".gz"


# --- Key generation ------------------------------------------------------


def test_key_contains_the_tenant() -> None:
    key = generate_object_key(TENANT)

    assert f"tenants/{TENANT}/" in key


def test_key_is_date_partitioned() -> None:
    key = generate_object_key(TENANT, now=datetime(2026, 3, 7, tzinfo=UTC))

    assert f"tenants/{TENANT}/2026/03/" in key


def test_key_object_name_is_a_uuid_not_the_filename() -> None:
    """Nothing about the upload should be inferable from the key."""
    key = generate_object_key(TENANT, filename="salary-review-2026.pdf")

    assert "salary-review" not in key
    assert key.endswith(".pdf")


def test_two_uploads_of_the_same_name_do_not_collide() -> None:
    first = generate_object_key(TENANT, filename="report.pdf")
    second = generate_object_key(TENANT, filename="report.pdf")

    assert first != second


def test_traversal_in_a_filename_cannot_escape_the_prefix() -> None:
    key = generate_object_key(TENANT, filename="../../../../etc/passwd")

    assert ".." not in key
    assert f"tenants/{TENANT}/" in key


def test_prefix_is_applied_and_normalised() -> None:
    key = generate_object_key(TENANT, prefix="/eu-west/")

    assert key.startswith("eu-west/tenants/")


def test_generated_keys_always_validate() -> None:
    for name in (None, "a.pdf", "../x", r"C:\a\b.docx", "no-extension", "x" * 300):
        assert validate_key(generate_object_key(TENANT, filename=name))


# --- Validation ----------------------------------------------------------


def test_empty_key_is_rejected() -> None:
    with pytest.raises(InvalidObjectKeyError, match="must not be empty"):
        validate_key("")


def test_absolute_key_is_rejected() -> None:
    with pytest.raises(InvalidObjectKeyError, match="must be relative"):
        validate_key("/tenants/x/y")


def test_traversal_segments_are_rejected() -> None:
    for key in ("a/../b", "../a", "a/b/..", "./a"):
        with pytest.raises(InvalidObjectKeyError, match="traversal"):
            validate_key(key)


def test_empty_segments_are_rejected() -> None:
    with pytest.raises(InvalidObjectKeyError, match="empty segments"):
        validate_key("a//b")


def test_control_characters_are_rejected() -> None:
    for key in ("a\x00b", "a\nb", "a\\b"):
        with pytest.raises(InvalidObjectKeyError, match="forbidden characters"):
            validate_key(key)


def test_padded_segments_are_rejected() -> None:
    with pytest.raises(InvalidObjectKeyError, match="padded"):
        validate_key("a/ b/c")


def test_overlong_key_is_rejected() -> None:
    with pytest.raises(InvalidObjectKeyError, match="exceeds"):
        validate_key("a" * (MAX_KEY_LENGTH + 1))


def test_ordinary_key_passes() -> None:
    assert validate_key("tenants/x/2026/03/abc.pdf")


# --- Tenant membership ---------------------------------------------------


def test_key_membership_recognises_its_own_tenant() -> None:
    key = generate_object_key(TENANT)

    assert key_belongs_to_tenant(key, TENANT) is True
    assert key_belongs_to_tenant(key, OTHER) is False


def test_membership_is_not_fooled_by_a_similar_prefix() -> None:
    """A tenant id appearing as a substring must not count as membership."""
    assert key_belongs_to_tenant(f"tenants/{TENANT}-evil/2026/03/x.pdf", TENANT) is False
