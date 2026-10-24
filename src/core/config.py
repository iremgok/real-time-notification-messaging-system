"""Application settings loaded from environment variables / .env (pydantic-settings)."""

from functools import cached_property
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "insecure-development-secret-change-me-0000000000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # --- General ---
    PROJECT_NAME: str = "Realtime Chat System"
    ENVIRONMENT: Literal["development", "production", "test"] = "development"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # --- Security / JWT ---
    SECRET_KEY: str = INSECURE_DEFAULT_SECRET
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1, le=60 * 24 * 7)
    CORS_ORIGINS: str = "http://localhost:8000"  # comma separated
    WS_ALLOWED_ORIGINS: str = ""  # comma separated; empty disables the Origin check

    # --- Database ---
    DATABASE_URL: str = "sqlite+aiosqlite:///./chat.db"
    # Run `alembic upgrade head` on startup. Convenient locally; in Docker the image CMD
    # migrates once before the workers start, so this is disabled there.
    DB_AUTO_MIGRATE: bool = True
    DB_POOL_SIZE: int = Field(default=10, ge=1)

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- WebSocket / messaging limits ---
    WS_MAX_MESSAGE_BYTES: int = Field(default=16 * 1024, ge=1024)
    WS_SEND_QUEUE_SIZE: int = Field(default=256, ge=8)
    WS_MAX_CONNECTIONS_PER_USER: int = Field(default=10, ge=1)
    MESSAGE_MAX_LENGTH: int = Field(default=4000, ge=1)
    PRESENCE_TTL_SECONDS: int = Field(default=60, ge=10)

    # --- Rate limiting (fixed window, backed by Redis) ---
    WS_RATE_LIMIT_COUNT: int = Field(default=20, ge=1)
    WS_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=10, ge=1)
    AUTH_RATE_LIMIT_COUNT: int = Field(default=10, ge=1)
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=60, ge=1)

    @field_validator("DATABASE_URL")
    @classmethod
    def _use_async_driver(cls, value: str) -> str:
        """Accept plain postgres URLs (as most PaaS providers emit) and switch to asyncpg."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix):]
        return value

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        if self.ENVIRONMENT != "production":
            return self
        if self.SECRET_KEY == INSECURE_DEFAULT_SECRET or len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY must be set to a random value of at least 32 chars in production")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must not contain '*' in production")
        return self

    @cached_property
    def cors_origins(self) -> list[str]:
        return _split_csv(self.CORS_ORIGINS)

    @cached_property
    def ws_allowed_origins(self) -> list[str]:
        return _split_csv(self.WS_ALLOWED_ORIGINS)

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


settings = Settings()
