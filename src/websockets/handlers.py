"""Handles inbound WebSocket frames: size/rate limits, validation and routing to services."""

from fastapi import status
from pydantic import ValidationError

from src.core.config import settings
from src.core.logger import get_logger
from src.models.database import SessionFactory
from src.models.entities import MessageStatus, User
from src.models.ws_schemas import (
    MessageAckEvent,
    PingEvent,
    SendMessageEvent,
    ServerEvent,
    client_event_adapter,
    error_event,
    server_event,
)
from src.services import chat_service, notification_service, presence_service
from src.services.exceptions import DomainError
from src.services.rate_limiter import ws_message_limiter
from src.websockets.connection import ClientConnection

logger = get_logger("ws.handlers")


class CloseConnection(Exception):
    """Raised by a handler when the session must end with a specific close code."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


async def handle_frame(conn: ClientConnection, raw: str) -> None:
    if conn.token_expired:
        raise CloseConnection(status.WS_1008_POLICY_VIOLATION, "Token expired")
    if len(raw.encode("utf-8")) > settings.WS_MAX_MESSAGE_BYTES:
        raise CloseConnection(status.WS_1009_MESSAGE_TOO_BIG, "Message too large")
    if not await ws_message_limiter.allow(str(conn.user_id)):
        conn.send(error_event("Rate limit exceeded, slow down"))
        return

    try:
        event = client_event_adapter.validate_json(raw)
    except ValidationError as exc:
        conn.send(error_event(_describe_validation_error(exc)))
        return

    await presence_service.refresh(conn.user_id)
    try:
        if isinstance(event, PingEvent):
            conn.send(server_event(ServerEvent.PONG))
        elif isinstance(event, SendMessageEvent):
            await _send_message(conn, event)
        elif isinstance(event, MessageAckEvent):
            await _acknowledge(conn, event)
    except DomainError as exc:
        conn.send(error_event(exc.message))


async def _send_message(conn: ClientConnection, event: SendMessageEvent) -> None:
    data = event.data
    room_id = conn.scope_room_id or data.room_id
    if room_id is None:
        conn.send(error_event("room_id is required on a multiplexed connection"))
        return
    if conn.scope_room_id is not None and data.room_id not in (None, conn.scope_room_id):
        conn.send(error_event("This connection is bound to a different room"))
        return

    async with SessionFactory() as session:
        sender = await session.get(User, conn.user_id)
        if sender is None or not sender.is_active:
            raise CloseConnection(status.WS_1008_POLICY_VIOLATION, "Account disabled")
        message = await chat_service.save_message(session, room_id, sender, data.content)

    conn.send(
        server_event(
            ServerEvent.MESSAGE_SENT,
            {"client_id": data.client_id, "id": str(message.id), "room_id": str(room_id)},
        )
    )
    await notification_service.message_created(message)


async def _acknowledge(conn: ClientConnection, event: MessageAckEvent) -> None:
    async with SessionFactory() as session:
        change = await chat_service.acknowledge(
            session, event.data.message_id, conn.user_id, MessageStatus(event.data.status)
        )
    if change is not None:
        await notification_service.message_status_changed(change)


def _describe_validation_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part not in ("data",))
    return f"Invalid event: {location} {first.get('msg', '')}".strip()

