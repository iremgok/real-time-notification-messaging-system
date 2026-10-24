"""Pydantic request/response schemas for the REST API."""

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from src.core.config import settings


def _as_utc(value: datetime) -> datetime:
    # SQLite drops tz info; everything is stored in UTC, so re-attach it for clients.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")]
MessageContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=settings.MESSAGE_MAX_LENGTH)
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth ---
class UserCreate(BaseModel):
    username: Username
    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def _bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:  # bcrypt only uses the first 72 bytes
            raise ValueError("password must be at most 72 bytes")
        return value


class UserRead(ORMModel):
    id: uuid.UUID
    username: str
    is_admin: bool
    created_at: UtcDatetime


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


# --- Rooms ---
class RoomCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None


class DirectRoomCreate(BaseModel):
    target_username: Username


class RoomRead(ORMModel):
    id: uuid.UUID
    name: str | None
    description: str | None
    is_group: bool
    created_at: UtcDatetime


# --- Messages ---
class MessageRead(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    sender_id: uuid.UUID
    sender_username: str
    content: str
    status: str
    created_at: UtcDatetime


class MessagePage(BaseModel):
    items: list[MessageRead]
    next_cursor: str | None = None
    has_more: bool


# --- Admin notifications ---
class NotificationCreate(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    body: MessageContent
    level: Literal["info", "warning", "critical"] = "info"


# --- Health ---
class HealthRead(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    redis: bool
    local_connections: int
