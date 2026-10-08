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

    # --- DEV-ONLY authentication shortcut --------------------------------
    # LOCAL DEVELOPMENT ONLY. When True, app.core.auth.get_auth_provider()
    # returns a DevAuthenticationProvider that treats the bearer token as a
    # user email, resolves it against the local `users` table, and returns
    # that user — so the frontend/Swagger can authenticate as a real DB
    # user while the real Pine Labs SSO/OIDC provider is still pending.
    #
    # Defaults to False, so unless a local .env explicitly sets it, behavior
    # is IDENTICAL to before (every request rejected by
    # UnconfiguredAuthenticationProvider). MUST remain False/unset in
    # staging and production — this is not a real authentication mechanism
    # (it verifies no token signature; it trusts the email as-is).
    DEV_AUTH_ENABLED: bool = False

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

    # --- Instance import (background job system) -------------------------
    # Bounds and knobs for the multi-file Instance Management import
    # (POST /instances/import -> background job). All have safe defaults so
    # the feature works out-of-the-box; override per-environment in .env.
    #
    # IMPORT_MAX_FILES: reject an upload that carries more than this many
    #   files. 0 (the default) means NO LIMIT — any number of files is
    #   accepted. Set a positive number to re-impose a per-request cap.
    # IMPORT_MAX_UPLOAD_BYTES: reject the request if the combined size of
    #   all uploaded files exceeds this (default 100 MB). Guards memory and
    #   temp-disk usage.
    # IMPORT_TEMP_DIR: directory the worker stages uploaded files in while
    #   a job runs (each file is written to disk on submit and read back by
    #   the worker, so request memory is released immediately). Empty means
    #   the OS default temp dir (tempfile.gettempdir()).
    # IMPORT_JOB_RETENTION_MINUTES: how long a finished job's row (and its
    #   staged files) are kept for progress/error polling before cleanup.
    #
    # NOTE: a job's files are always processed SEQUENTIALLY (one at a time)
    # — a parallel/concurrency mode was prototyped and removed for now, so
    # there is deliberately no worker-mode/concurrency setting here.
    IMPORT_MAX_FILES: int = 0  # 0 = unlimited
    IMPORT_MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024
    IMPORT_TEMP_DIR: str = ""
    IMPORT_JOB_RETENTION_MINUTES: int = 1440

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
