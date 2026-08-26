import os
from typing import Optional
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )

    PROJECT_NAME: str = "Real-Time Notification & Messaging System"
    API_V1_STR: str = "/api/v1"

    # JWT Settings
    SECRET_KEY: str = Field(
        default="SUPER_SECRET_SIGNING_KEY_CHANGE_IN_PRODUCTION_1234567890"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 Days

    # Database Configuration (PostgreSQL)
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    POSTGRES_DB: str = Field(default="messaging_db")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Redis Configuration
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_PASSWORD: Optional[str] = Field(default=None)

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # WebSocket Configuration
    WS_MAX_MESSAGE_SIZE: int = 65536  # 64 KB
    WS_RATE_LIMIT_LIMIT: int = 15  # Max messages
    WS_RATE_LIMIT_WINDOW: int = 10  # in seconds window

    # Presence Settings
    PRESENCE_EXPIRE_SECONDS: int = 30
    PRESENCE_HEARTBEAT_INTERVAL: int = 15


settings = Settings()
