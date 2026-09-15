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

    # --- Regions and residency ---------------------------------------
    #: The region this deployment serves when a tenant assigns none.
    default_region: str = "local"

    #: Optional ``region=url,region=url`` map. Empty means single-region:
    #: `default_region` is served by `database_url`, which is P02 behaviour.
    database_urls: str = ""

    #: Optional ``region=bucket,region=bucket`` map. Empty means every region
    #: resolves to `s3_bucket`.
    s3_buckets: str = ""

    # --- Object storage ----------------------------------------------
    #: Empty means "use the provider's default endpoint" -- i.e. real AWS S3.
    #: Anything else points at an S3-compatible server such as MinIO.
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "apex-objects"
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    #: MinIO and most compatible servers need path-style addressing; AWS S3
    #: itself wants virtual-host style.
    s3_force_path_style: bool = True
    s3_connect_timeout: int = 5
    s3_read_timeout: int = 30

    storage_max_object_bytes: int = 100 * 1024 * 1024
    storage_signed_url_ttl_seconds: int = 300
    #: Comma-separated allowlist. Empty means any type is accepted.
    storage_allowed_content_types: str = ""

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

    @property
    def allowed_content_type_set(self) -> frozenset[str]:
        """Permitted content types. Empty means no restriction."""
        return frozenset(
            item.strip().lower()
            for item in self.storage_allowed_content_types.split(",")
            if item.strip()
        )

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
