"""
Application configuration, loaded from environment variables (and a local
.env file in development) via pydantic-settings.

No credentials are hardcoded here. DATABASE_URL has no default — it must
be supplied via the environment or a local, untracked .env file (see
.env.example for the expected shape).
"""
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # "development" | "test" | "staging" | "production"
    ENVIRONMENT: str = "development"

    # --- Authentication (Part 14) ---------------------------------------
    # Provider-neutral placeholders only — NOT wired to any real identity
    # provider. app.core.auth.get_auth_provider() uses their presence
    # (both set) as the sole switch for "is a real provider configured",
    # and today both are always unset, so every request is rejected by
    # UnconfiguredAuthenticationProvider regardless of what these hold.
    # No real Pine Labs issuer/audience value exists yet — see
    # app/core/auth.py's module docstring. Never put a client secret
    # here or anywhere else in this file.
    AUTH_ISSUER: Optional[str] = None
    AUTH_AUDIENCE: Optional[str] = None

    # SQLAlchemy connection string, e.g.
    # postgresql+psycopg2://user:password@host:5432/dbname
    # Required — intentionally has no default so a missing configuration
    # fails fast instead of silently pointing at some guessed database.
    DATABASE_URL: str

    # Frontend origin(s) allowed to call this API, as a raw comma-separated
    # string (e.g. "http://localhost:5173,https://app.example.com").
    # Kept as `str` (not `List[str]`) because pydantic-settings tries to
    # JSON-decode env values for complex/list-typed fields, which breaks a
    # plain comma-separated value. Use the `cors_origins` property below to
    # get the parsed list.
    CORS_ORIGINS: str = ""

    API_V1_PREFIX: str = "/api/v1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached Settings instance — environment is read once per process."""
    return Settings()
