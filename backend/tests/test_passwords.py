"""Password hashing tests. No database required."""

from __future__ import annotations

import pytest

from app.identity.passwords import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)

PASSWORD = "correct horse battery staple"


def test_hash_is_argon2id() -> None:
    """Argon2id is the memory-hard variant OWASP recommends."""
    assert hash_password(PASSWORD).startswith("$argon2id$")


def test_hash_does_not_contain_the_password() -> None:
    assert PASSWORD not in hash_password(PASSWORD)


def test_hashes_are_salted() -> None:
    """Identical passwords must not produce identical hashes."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_correct_password_verifies() -> None:
    assert verify_password(PASSWORD, hash_password(PASSWORD)) is True


def test_wrong_password_is_rejected() -> None:
    assert verify_password("not the password", hash_password(PASSWORD)) is False


def test_verification_returns_false_rather_than_raising() -> None:
    """Callers must not have to distinguish exception types to deny access."""
    assert verify_password(PASSWORD, "not-a-valid-hash") is False
    assert verify_password("", hash_password(PASSWORD)) is False


def test_empty_password_cannot_be_hashed() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password("")


def test_current_hash_does_not_need_rehashing() -> None:
    assert needs_rehash(hash_password(PASSWORD)) is False


def test_unreadable_hash_is_treated_as_needing_rehash() -> None:
    assert needs_rehash("garbage") is True


# --- Refresh tokens ------------------------------------------------------


def test_refresh_tokens_are_unique() -> None:
    assert generate_refresh_token() != generate_refresh_token()


def test_refresh_tokens_carry_enough_entropy() -> None:
    """48 random bytes, url-safe encoded."""
    assert len(generate_refresh_token()) >= 60


def test_refresh_token_hash_is_stable_and_hides_the_token() -> None:
    token = generate_refresh_token()
    digest = hash_refresh_token(token)

    assert digest == hash_refresh_token(token)
    assert token not in digest
    assert len(digest) == 64
