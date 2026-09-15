"""Request and response models for the identity API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    """Credentials presented at login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    """A refresh token presented for exchange."""

    refresh_token: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    """A freshly minted token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 -- scheme name, not a secret
    expires_in: int


class UserSummary(BaseModel):
    """A user, as returned to clients. Never carries the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    last_login_at: datetime | None = None


class CurrentUserResponse(UserSummary):
    """The caller's own record, with the authorisation they hold."""

    tenant_id: uuid.UUID
    permissions: list[str]


class RoleSummary(BaseModel):
    """A role within a tenant."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    is_provisional: bool
