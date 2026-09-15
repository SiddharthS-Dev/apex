"""Redaction tests. No database required.

An audit record cannot be edited after the fact, so a credential written into
one cannot be removed. Redaction on the way in is the only line of defence
these tests protect.
"""

from __future__ import annotations

from app.core.redaction import (
    MAX_DEPTH,
    MAX_VALUE_LENGTH,
    REDACTED,
    is_sensitive_key,
    redact,
)


def test_none_and_empty_become_an_empty_dict() -> None:
    assert redact(None) == {}
    assert redact({}) == {}


def test_ordinary_values_survive() -> None:
    assert redact({"action": "login", "count": 3, "ok": True}) == {
        "action": "login",
        "count": 3,
        "ok": True,
    }


# --- Sensitive keys ------------------------------------------------------


def test_obvious_credentials_are_redacted() -> None:
    result = redact(
        {
            "password": "hunter2",
            "access_token": "eyJhbGciOi",
            "api_key": "sk-live-1234",
            "authorization": "Bearer abc",
        }
    )

    assert set(result.values()) == {REDACTED}


def test_redaction_marks_rather_than_removes() -> None:
    """The record should still show that a field was present."""
    result = redact({"password": "hunter2"})

    assert "password" in result
    assert result["password"] == REDACTED


def test_key_matching_is_case_insensitive_and_substring() -> None:
    result = redact({"UserPassword": "x", "refreshToken": "y", "X-Api-Key": "z"})

    assert set(result.values()) == {REDACTED}


def test_over_redaction_is_the_intended_direction() -> None:
    """A harmless field caught by the deny-list is an acceptable loss."""
    assert is_sensitive_key("token_count") is True
    assert redact({"token_count": 5})["token_count"] == REDACTED


def test_unrelated_keys_are_not_redacted() -> None:
    assert is_sensitive_key("action") is False
    assert redact({"action": "login"})["action"] == "login"


# --- Nesting -------------------------------------------------------------


def test_nested_credentials_are_redacted() -> None:
    result = redact({"outer": {"inner": {"password": "hunter2", "user": "ada"}}})

    assert result["outer"]["inner"]["password"] == REDACTED
    assert result["outer"]["inner"]["user"] == "ada"


def test_credentials_inside_lists_are_redacted() -> None:
    result = redact({"items": [{"secret": "s"}, {"name": "n"}]})

    assert result["items"][0]["secret"] == REDACTED
    assert result["items"][1]["name"] == "n"


def test_excessive_depth_is_collapsed() -> None:
    """A pathologically deep payload must not turn a write into a hang."""
    payload: dict[str, object] = {"v": "leaf"}
    for _ in range(MAX_DEPTH + 4):
        payload = {"nested": payload}

    assert "max depth" in str(redact(payload))


# --- Size ----------------------------------------------------------------


def test_long_strings_are_truncated() -> None:
    result = redact({"blob": "x" * (MAX_VALUE_LENGTH + 500)})

    assert result["blob"].endswith("...[truncated]")
    assert len(result["blob"]) <= MAX_VALUE_LENGTH + len("...[truncated]")


def test_short_strings_are_untouched() -> None:
    assert redact({"blob": "short"})["blob"] == "short"


# --- Robustness ----------------------------------------------------------


def test_unexpected_types_are_rendered_not_refused() -> None:
    """An audit write must not fail because a caller passed an odd type."""

    class Odd:
        def __str__(self) -> str:
            return "odd-value"

    assert redact({"thing": Odd()})["thing"] == "odd-value"


def test_non_string_keys_are_coerced() -> None:
    assert redact({1: "one"}) == {"1": "one"}


def test_sets_and_tuples_become_lists() -> None:
    result = redact({"tags": ("a", "b")})

    assert result["tags"] == ["a", "b"]


def test_truncation_can_be_disabled() -> None:
    """Integration events enforce a total size limit instead of truncating.

    A consumer acting on a silently shortened value is worse than a producer
    refusing an oversized payload.
    """
    long_value = "x" * (MAX_VALUE_LENGTH + 500)

    result = redact({"blob": long_value}, max_value_length=None)

    assert result["blob"] == long_value


def test_disabling_truncation_still_redacts() -> None:
    result = redact({"password": "hunter2", "note": "n"}, max_value_length=None)

    assert result["password"] == REDACTED
    assert result["note"] == "n"
