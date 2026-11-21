"""Application settings, loaded from environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    llm_api_key: SecretStr
    llm_model: str = "claude-sonnet-5-5"
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    db_path: Path = Path("data/incidents.db")
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def load_settings() -> Settings:
    """Load settings, turning validation failures into an actionable message."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            var = str(err["loc"][0]).upper() if err["loc"] else "?"
            if var == "LLM_API_KEY" and err["type"] == "missing":
                problems.append(
                    "LLM_API_KEY is not set. Set it before starting, e.g.\n"
                    "    export LLM_API_KEY=<your-provider-key>\n"
                    "  Use LLM_API_KEY=dummy to run every endpoint except /assist."
                )
            else:
                problems.append(f"{var}: {err['msg']}")
        raise ConfigError("Invalid configuration:\n  " + "\n  ".join(problems)) from None
