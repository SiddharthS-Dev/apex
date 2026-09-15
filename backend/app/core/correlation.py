"""Correlation identifiers.

A correlation id ties everything one user action caused -- audit records across
several bounded contexts, integration events, and later agent executions --
into a single traceable chain. Without it, reconstructing "what happened when
this asset was published" means guessing from timestamps.

The id is carried in a context variable rather than threaded through every
signature, because the code that needs it (the audit writer) is several layers
below the code that has it (the request).

**A correlation id carries no authority.** It is accepted from the caller so a
client can tie its own logs to ours, but it names nothing and grants nothing.
It is still validated as a UUID before use -- an unvalidated value from a
request ends up in stored records, and arbitrary text there is a log-injection
surface.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

#: Header a client may use to supply its own correlation id.
CORRELATION_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[uuid.UUID | None] = ContextVar(
    "apex_correlation_id",
    default=None,
)
_causation_id: ContextVar[uuid.UUID | None] = ContextVar(
    "apex_causation_id",
    default=None,
)


def current_correlation_id() -> uuid.UUID | None:
    """The correlation id for the current operation, if one is set."""
    return _correlation_id.get()


def current_causation_id() -> uuid.UUID | None:
    """The id of the event that caused the current one, if any."""
    return _causation_id.get()


def correlation_id_or_new() -> uuid.UUID:
    """The current correlation id, or a fresh one.

    Used by audit writes: a record without a correlation id is far less useful
    than one with an isolated id, so this never returns ``None``.
    """
    return _correlation_id.get() or uuid.uuid4()


@contextmanager
def correlation_scope(
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
) -> Iterator[uuid.UUID]:
    """Bind a correlation id for the duration of the block."""
    resolved = correlation_id or uuid.uuid4()
    correlation_token: Token[uuid.UUID | None] = _correlation_id.set(resolved)
    causation_token: Token[uuid.UUID | None] = _causation_id.set(causation_id)
    try:
        yield resolved
    finally:
        _causation_id.reset(causation_token)
        _correlation_id.reset(correlation_token)


def parse_correlation_header(raw: str | None) -> uuid.UUID | None:
    """Parse a client-supplied header value, rejecting anything malformed."""
    if not raw:
        return None
    try:
        return uuid.UUID(raw.strip())
    except ValueError:
        return None


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Bind a correlation id to every request and echo it back.

    Honours a valid ``X-Correlation-ID`` from the caller so distributed traces
    join up, and generates one otherwise.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = parse_correlation_header(request.headers.get(CORRELATION_HEADER))
        with correlation_scope(supplied) as correlation_id:
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = str(correlation_id)
            return response
