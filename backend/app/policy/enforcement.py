"""Enforcement -- stage four of the pipeline.

    Policy Definition -> Attribute Resolution -> Policy Decision -> **Enforcement**

The policy enforcement point is deliberately thin: it turns a
:class:`~app.policy.engine.Decision` into an HTTP outcome and does nothing
else. All judgement lives in the evaluator, which is why the evaluator can be
tested without a web framework and this module needs almost no tests of its own.

**Default-deny.** Anything other than ``ALLOW`` refuses the request, including
``NOT_APPLICABLE``. A request no policy speaks to is not permitted by silence.

This composes with, and does not replace, the RBAC check from Commit 004.
``require_permission`` asks *may this role do this kind of thing at all*;
``require_access`` asks *may this principal do it to this resource, given the
attributes*. A route may use either or both.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.dependencies import CurrentPrincipal, DbSession, Principal
from app.policy.attributes import AttributeRegistry, access_request
from app.policy.engine import Decision, Outcome
from app.policy.service import decide


class AccessDeniedError(Exception):
    """A decision refused the request."""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(f"Access {decision.outcome.value} for {decision.request.action}")


def enforce(decision: Decision) -> None:
    """Raise unless the decision allows the request."""
    if decision.outcome is not Outcome.ALLOW:
        raise AccessDeniedError(decision)


def require_access(
    action: str,
    resource_type: str,
    *,
    registry: AttributeRegistry | None = None,
) -> Callable[[Principal, AsyncSession], Coroutine[Any, Any, Principal]]:
    """Build a dependency that runs the PDP for one action and resource type.

    The tenant comes from the authenticated principal's token, never from the
    request body or a header, so a caller cannot steer the decision at another
    tenant's policies.
    """

    async def _check(principal: CurrentPrincipal, session: DbSession) -> Principal:
        request = access_request(
            tenant_id=principal.tenant_id,
            subject_id=principal.user_id,
            action=action,
            resource_type=resource_type,
        )
        decision = await decide(session, request, registry=registry)

        if decision.outcome is not Outcome.ALLOW:
            # 403: the caller is authenticated, and policy refused them. The
            # reason is not returned -- it would describe the tenant's rules to
            # someone the rules just refused.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied for '{action}' on '{resource_type}'",
            )
        return principal

    return _check
