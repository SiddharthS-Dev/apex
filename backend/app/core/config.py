"""Application configuration, loaded from the environment.

Every setting is read from an ``APEX_``-prefixed environment variable so that
container, CI and local configuration all share one naming convention. See
``.env.example`` at the repository root for the full set.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Comma-separated so the value stays readable in .env and compose files.
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, ignoring blank entries."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
