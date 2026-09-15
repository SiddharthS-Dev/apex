"""Response models for the audit read API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditEventResponse(BaseModel):
    """One audit record, as returned to a client.

    ``context``, ``before_state`` and ``after_state`` are already redacted --
    redaction happens on write, so nothing sensitive is stored to begin with.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_type: str
    actor_id: uuid.UUID | None
    actor_label: str | None
    action: str
    outcome: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    resource_label: str | None
    occurred_at: datetime
    recorded_at: datetime
    correlation_id: uuid.UUID
    causation_id: uuid.UUID | None
    ip_address: str | None
    user_agent: str | None
    context: dict[str, Any]
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
