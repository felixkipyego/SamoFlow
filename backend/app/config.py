# backend/app/config.py
# Settings module (Task 1.1.c): validates the five required environment
# variables at startup and exposes them through one cached Settings
# instance. Nothing here reads the environment or calls get_settings() at
# import time (module import must succeed with an empty environment).
#
# ASSUMPTION: pydantic and pydantic-settings are not new dependencies here.
# pydantic-settings==2.15.0 is already an approved runtime dependency
# (Step 1.1.a); pydantic arrives transitively through it and fastapi.
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# SQLAlchemy 2.0 maps a plain "postgresql://" DSN to the psycopg2 driver,
# which this project does not install (Step 1.1.a installs psycopg[binary],
# i.e. psycopg 3). This scheme selects the psycopg (v3) driver instead.
_POSTGRES_SCHEME = "postgresql+psycopg"


class Settings(BaseSettings):
    # frozen: instances are immutable after construction.
    # hide_input_in_errors: hides the raw offending value from str()/repr()
    # of a ValidationError, but not from its structured .errors() list
    # (verified against pydantic 2.13.5). get_settings() below is the
    # boundary that turns a ValidationError into an input-free SettingsError;
    # calling Settings() directly still risks a raw value in .errors(), so
    # startup code must always go through get_settings().
    model_config = SettingsConfigDict(frozen=True, hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"]
    database_url: SecretStr
    qdrant_url: str
    api_host: str = Field(min_length=1)
    api_port: int = Field(ge=1, le=65535)

    @field_validator("database_url")
    @classmethod
    def _require_postgres_psycopg_scheme(cls, value: SecretStr) -> SecretStr:
        parts = urlsplit(value.get_secret_value())
        database_name = parts.path.lstrip("/")
        if parts.scheme != _POSTGRES_SCHEME or not parts.hostname or not database_name:
            # Message names only the required scheme, never the value under
            # validation, so a bad DSN's password cannot leak here.
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL using the "
                f"'{_POSTGRES_SCHEME}' scheme, with a hostname and a "
                "database name (SQLAlchemy 2.0 defaults plain "
                "'postgresql://' to psycopg2, which is not installed)."
            )
        return value

    @field_validator("qdrant_url")
    @classmethod
    def _require_http_scheme(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("QDRANT_URL must be an http or https URL.")
        return value

    def database_url_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.database_url.get_secret_value()


class SettingsError(Exception):
    # Raised by get_settings() instead of pydantic's ValidationError. Its
    # message is a plain str captured from str(exc) before this exception is
    # raised, so it carries no reference to the original ValidationError
    # (no __cause__/__context__) and no raw input value.
    pass


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        message = str(exc)
    # Raised after leaving the except block on purpose: this is no longer
    # "while handling" exc, so Python does not implicitly chain __context__.
    raise SettingsError(message)
