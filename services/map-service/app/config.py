"""Map service configuration — driven by environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration for the map service.

    Values are read from environment variables (case-insensitive).
    A ``.env`` file in the working directory is loaded automatically.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str = "postgresql+asyncpg://marga:marga@localhost:5432/marga"
    SERVICE_NAME: str = "map-service"
    # Log level forwarded to uvicorn/standard logging
    LOG_LEVEL: str = "info"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton Settings instance (lazy-initialised)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
