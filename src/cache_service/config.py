"""Service configuration, read from ``CACHE_SERVICE_*`` environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CACHE_SERVICE_", env_file=".env", extra="ignore"
    )

    database_url: str = "sqlite+aiosqlite:///./cache.db"
    log_level: str = "INFO"

    # The transformer stands in for a remote service, so it gets the knobs a
    # real client needs: a timeout and a cap on parallel calls. The latency only
    # exists to make the simulated service feel remote.
    transformer_latency: float = Field(default=0.1, ge=0)
    transformer_timeout: float = Field(default=5.0, gt=0)
    transformer_max_concurrency: int = Field(default=10, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
