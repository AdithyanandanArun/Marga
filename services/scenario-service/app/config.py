"""Service configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATA_DIR: Path = Path("./data")
    SERVICE_NAME: str = "scenario-service"
    SIMULATION_ADAPTER_URL: str = "http://localhost:8001"
    GATEWAY_URL: str = "http://localhost:8000"

    model_config = {"env_prefix": "SCENARIO_", "env_file": ".env", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
