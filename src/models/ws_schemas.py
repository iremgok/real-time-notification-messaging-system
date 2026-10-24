"""WebSocket protocol: inbound client events (validated) and outbound server events.

Client -> server frames (JSON text):
    {"type": "ping"}
    {"type": "send_message", "data": {"room_id": "<uuid, optional on /ws/{room_id}>",
                                      "content": "...", "client_id": "<optional correlation id>"}}
    {"type": "message_ack", "data": {"message_id": "<uuid>", "status": "DELIVERED" | "READ"}}

Server -> client frames: {"type": <ServerEvent>, "data": {...}}
"""

import uuid
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from src.models.schemas import MessageContent


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PingEvent(_Strict):
    type: Literal["ping"]


class SendMessageData(_Strict):
    room_id: uuid.UUID | None = None
    content: MessageContent
    client_id: Annotated[str, StringConstraints(max_length=64)] | None = None


class SendMessageEvent(_Strict):
    type: Literal["send_message"]
    data: SendMessageData


class MessageAckData(_Strict):
    message_id: uuid.UUID
    status: Literal["DELIVERED", "READ"]


class MessageAckEvent(_Strict):
    type: Literal["message_ack"]
    data: MessageAckData


ClientEvent = Annotated[Union[PingEvent, SendMessageEvent, MessageAckEvent], Field(discriminator="type")]
client_event_adapter: TypeAdapter[ClientEvent] = TypeAdapter(ClientEvent)


class ServerEvent(str, Enum):
    CONNECTED = "connected"
    PONG = "pong"
    MESSAGE = "message"
    MESSAGE_SENT = "message_sent"
    MESSAGE_STATUS = "message_status_update"
    PRESENCE = "presence_change"
    JOINED_ROOM = "joined_room"
    LEFT_ROOM = "left_room"
    NOTIFICATION = "notification"
    ERROR = "error"


def server_event(event: ServerEvent, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": event.value, "data": data or {}}


def error_event(message: str) -> dict[str, Any]:
    return server_event(ServerEvent.ERROR, {"message": message})
