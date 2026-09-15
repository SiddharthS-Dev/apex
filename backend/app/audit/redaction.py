"""Redaction for audit metadata.

Audit records are long-lived, widely readable within a tenant, and by design
cannot be edited after the fact. A credential written into one is therefore
worse than a credential in a log file: it cannot be removed, only followed by a
note saying it should not have been there.

So redaction happens on the way *in*, not on the way out. A caller cannot opt
out, because the whole point is that the caller may not have noticed what was
in the payload they passed.

Matching is on the key, by substring, case-insensitively. That over-redacts --
a field called ``token_count`` loses its value -- and that is the intended
direction to err. An audit record missing a harmless number is a small loss; an
audit record containing a live refresh token is an incident.
"""

from __future__ import annotations

from typing import Any

#: Substrings that mark a key as sensitive.
SENSITIVE_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "auth_header",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "credential",
        "cookie",
        "session_key",
        "otp",
        "pin",
        "signature",
        "salt",
        "hash",
    }
)

#: What a redacted value is replaced with. A marker rather than removal, so the
#: record still shows that a field was present.
REDACTED = "[redacted]"

#: Longest string kept verbatim in metadata. Audit is a record, not a store.
MAX_VALUE_LENGTH = 2000

#: Deepest nesting walked before collapsing. Guards against a cyclic or
#: pathologically deep payload turning an audit write into a hang.
MAX_DEPTH = 8


def is_sensitive_key(key: str) -> bool:
    """Whether a key name suggests the value must not be stored.

    Separators are normalised before matching, so the HTTP-header spelling
    ``X-Api-Key`` is caught by the same fragment as ``api_key``. Without this,
    header names -- the most likely place for a live credential to arrive --
    would slip past.
    """
    normalised = key.lower().replace("-", "_").replace(" ", "_")
    return any(fragment in normalised for fragment in SENSITIVE_KEY_FRAGMENTS)


def _redact_value(value: Any, depth: int) -> Any:
    if depth >= MAX_DEPTH:
        return "[truncated: max depth]"

    if isinstance(value, dict):
        return {
            str(key): (
                REDACTED if is_sensitive_key(str(key)) else _redact_value(item, depth + 1)
            )
            for key, item in value.items()
        }

    if isinstance(value, list | tuple | set | frozenset):
        return [_redact_value(item, depth + 1) for item in value]

    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH] + "...[truncated]"

    if isinstance(value, str | int | float | bool) or value is None:
        return value

    # Anything else is rendered as text rather than refused: an audit write
    # must not fail because a caller passed an unexpected type.
    rendered = str(value)
    if len(rendered) > MAX_VALUE_LENGTH:
        return rendered[:MAX_VALUE_LENGTH] + "...[truncated]"
    return rendered


def redact(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``payload`` safe to persist in an audit record."""
    if not payload:
        return {}
    result = _redact_value(payload, 0)
    return result if isinstance(result, dict) else {}
