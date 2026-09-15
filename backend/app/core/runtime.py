"""Process-level runtime configuration that must happen before the event loop.

psycopg's async driver cannot run on the ``ProactorEventLoop`` that asyncio
selects by default on Windows, and raises ``InterfaceError`` on first connect.
Linux and macOS are unaffected, so without this the platform would have a
defect that appears only on developer machines and never in CI or production --
the worst place for one to hide.

:func:`configure_event_loop_policy` switches Windows to the selector loop. It
must run *before* any event loop is created, so it is called at import time by
:mod:`app.core.database` -- the module you cannot avoid importing if you intend
to talk to the database.

**Forward compatibility.** The event loop *policy* API is deprecated and slated
for removal in Python 3.16. It is used here regardless because it is the only
mechanism that influences the loop uvicorn creates for us; we do not own that
call site and so cannot pass ``loop_factory``. The implementation degrades to a
no-op rather than raising if the API disappears. When that happens, Windows
development will instead need the loop selected at the server entry point --
tracked as assumption **A-15**.
"""

from __future__ import annotations

import asyncio
import sys
import warnings


def configure_event_loop_policy() -> None:
    """Select an event loop implementation psycopg can use.

    No-op on platforms whose default loop is already compatible, and on Python
    versions where the policy API has been removed.
    """
    if sys.platform != "win32":
        return

    # Attribute access on the deprecated names is itself what emits the
    # warning, so the lookups happen inside the suppression block. We are
    # acting on the deprecation deliberately, not ignoring it -- see the
    # module docstring.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        policy_type = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        get_policy = getattr(asyncio, "get_event_loop_policy", None)
        set_policy = getattr(asyncio, "set_event_loop_policy", None)

        if policy_type is None or get_policy is None or set_policy is None:
            return

        if isinstance(get_policy(), policy_type):
            return

        set_policy(policy_type())
