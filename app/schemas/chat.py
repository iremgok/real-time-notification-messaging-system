import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class RoomCreate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    is_group: bool = Field(default=True)


class RoomResponse(BaseModel):
    id: uuid.UUID
    name: Optional[str]
    description: Optional[str]
    is_group: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    sender_id: uuid.UUID
    sender_username: str
    content: str
    created_at: datetime
    status: str

    class Config:
        from_attributes = True


class MessageCursorPage(BaseModel):
    items: List[MessageResponse]
    next_cursor: Optional[str] = None
    has_more: bool
