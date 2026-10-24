"""Publishes real-time events to Redis so every instance can deliver them.

REST routes and WebSocket handlers both go through here; neither needs to know
about channel names or the transport.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from src.core.logger import get_logger
from src.models.schemas import MessageRead, NotificationCreate
from src.models.ws_schemas import ServerEvent, server_event
from src.services.chat_service import StatusChange
from src.services.redis_service import BROADCAST_CHANNEL, redis_service, room_channel, user_channel

logger = get_logger("notify")


async def _publish(channel: str, payload: dict[str, Any]) -> None:
    try:
        await redis_service.publish(channel, payload)
    except Exception:
        # Data is already persisted; clients recover it via the history endpoint.
        logger.exception("Failed to publish event type=%s", payload.get("type"))


async def message_created(message: MessageRead) -> None:
    await _publish(room_channel(message.room_id), server_event(ServerEvent.MESSAGE, message.model_dump(mode="json")))


async def message_status_changed(change: StatusChange) -> None:
    data = {"message_id": str(change.message_id), "room_id": str(change.room_id), "status": change.status.value}
    await _publish(room_channel(change.room_id), server_event(ServerEvent.MESSAGE_STATUS, data))


async def presence_changed(user_id: uuid.UUID, room_ids: Iterable[uuid.UUID], online: bool) -> None:
    payload = server_event(
        ServerEvent.PRESENCE, {"user_id": str(user_id), "status": "online" if online else "offline"}
    )
    for room_id in room_ids:
        await _publish(room_channel(room_id), payload)


async def membership_changed(user_id: uuid.UUID, room_id: uuid.UUID, joined: bool) -> None:
    """Tell every instance to (un)subscribe the user's live sockets to the room."""
    event = ServerEvent.JOINED_ROOM if joined else ServerEvent.LEFT_ROOM
    await _publish(user_channel(user_id), server_event(event, {"room_id": str(room_id)}))


def _notification_payload(notification: NotificationCreate, sender: str) -> dict[str, Any]:
    return server_event(
        ServerEvent.NOTIFICATION,
        {
            **notification.model_dump(),
            "sender": sender,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    )


async def broadcast(notification: NotificationCreate, sender: str) -> None:
    await _publish(BROADCAST_CHANNEL, _notification_payload(notification, sender))


async def notify_user(user_id: uuid.UUID, notification: NotificationCreate, sender: str) -> None:
    await _publish(user_channel(user_id), _notification_payload(notification, sender))
