"""Application settings, loaded from environment variables.

Field names match the environment variable names exactly.
"""

import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    LLM_PROVIDER: Literal["anthropic", "openai"] = "anthropic"
    # Optional so every other endpoint runs without it; /assist fails fast when unset.
    LLM_API_KEY: SecretStr | None = None
    LLM_MODEL: str = "claude-sonnet-5-5"
    LLM_BASE_URL: str | None = None
    LLM_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, le=120)

    DB_PATH: Path = Path("data/incidents.db")
    PORT: int = Field(default=8000, ge=1, le=65535)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("LLM_API_KEY", mode="before")
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        # `LLM_API_KEY=` in a .env file means "not set", not an empty key.
        return None if isinstance(value, str) and not value.strip() else value


def load_settings() -> Settings:
    """Load settings, or exit the process with an actionable message."""
    try:
        return Settings()
    except ValidationError as exc:
        sys.exit(f"Invalid configuration. Fix these environment variables:\n\n{exc}")
