"""Redaction for anything persisted from caller-supplied data.

Used by audit records and by integration event payloads. Both are long-lived,
widely readable within a tenant, and leave the process -- an event payload
reaches whatever consumes the outbox. A credential written into either is worse
than one in a log file: it cannot be recalled.

This lives in ``core`` rather than in ``audit`` because Events would otherwise
have to depend on Audit to reuse it, which the context map forbids (Audit is
written to by every context and depended on by none).

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

#: Longest string kept verbatim by default. Audit is a record, not a store, so
#: a truncated value there is an acceptable loss.
#:
#: Callers that must not lose data pass ``max_value_length=None`` and enforce a
#: size limit of their own instead. Integration events do exactly that: a
#: consumer acting on a silently shortened value is worse than a producer
#: refusing an oversized one.
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


def _truncate(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _redact_value(value: Any, depth: int, limit: int | None) -> Any:
    if depth >= MAX_DEPTH:
        return "[truncated: max depth]"

    if isinstance(value, dict):
        return {
            str(key): (
                REDACTED
                if is_sensitive_key(str(key))
                else _redact_value(item, depth + 1, limit)
            )
            for key, item in value.items()
        }

    if isinstance(value, list | tuple | set | frozenset):
        return [_redact_value(item, depth + 1, limit) for item in value]

    if isinstance(value, str):
        return _truncate(value, limit)

    if isinstance(value, int | float | bool) or value is None:
        return value

    # Anything else is rendered as text rather than refused: a write must not
    # fail because a caller passed an unexpected type.
    return _truncate(str(value), limit)


def redact(
    payload: dict[str, Any] | None,
    *,
    max_value_length: int | None = MAX_VALUE_LENGTH,
) -> dict[str, Any]:
    """Return a copy of ``payload`` safe to persist.

    ``max_value_length=None`` disables truncation, for callers that enforce a
    total size limit instead and must not silently lose data.
    """
    if not payload:
        return {}
    result = _redact_value(payload, 0, max_value_length)
    return result if isinstance(result, dict) else {}
