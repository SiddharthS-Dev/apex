"""Application configuration, loaded from the environment.

Every setting is read from an ``APEX_``-prefixed environment variable so that
container, CI and local configuration all share one naming convention. See
``.env.example`` at the repository root for the full set.
"""

from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Placeholder signing key. Usable in development so the app starts out of the
#: box; refused anywhere else. Generate a real one with `openssl rand -hex 32`.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    """Runtime settings for the APEX backend."""

    model_config = SettingsConfigDict(
        env_prefix="APEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    api_prefix: str = "/api/v1"
    backend_host: str = "0.0.0.0"  # noqa: S104 -- bound inside a container
    backend_port: int = 8000

    # --- Database ---------------------------------------------------
    # psycopg 3 serves both the sync and async engines, so one URL covers
    # the application and Alembic.
    database_url: str = "postgresql+psycopg://apex:apex@localhost:5432/apex"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_echo: bool = False

    # --- Authentication ---------------------------------------------
    # The default is a development placeholder. `_reject_default_secret`
    # below refuses to let it reach a non-development environment.
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14

    # Comma-separated so the value stays readable in .env and compose files.
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, ignoring blank entries."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _reject_default_secret(self) -> Self:
        """Refuse to start outside development with the placeholder key.

        A signing key that ships in the repository is not a secret. Failing at
        startup is loud; silently signing production tokens with a public
        constant is not.
        """
        if self.env != "development" and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError(
                "APEX_JWT_SECRET is still the development placeholder. "
                "Set a real value (openssl rand -hex 32) for "
                f"APEX_ENV={self.env!r}."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
