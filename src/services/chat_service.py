"""Message persistence, delivery-status transitions and keyset-paginated history."""

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.entities import Message, MessageStatus, User
from src.models.schemas import MessagePage, MessageRead
from src.services import room_service
from src.services.exceptions import InvalidInputError, NotFoundError, PermissionDeniedError


@dataclass(frozen=True)
class StatusChange:
    message_id: uuid.UUID
    room_id: uuid.UUID
    status: MessageStatus


def _to_read(message: Message, sender_username: str) -> MessageRead:
    return MessageRead(
        id=message.id,
        room_id=message.room_id,
        sender_id=message.sender_id,
        sender_username=sender_username,
        content=message.content,
        status=message.status,
        created_at=message.created_at,
    )


# --- Cursor encoding: opaque "<created_at ISO>|<message uuid>" (base64url) ---
def _encode_cursor(created_at: datetime, message_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{message_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_raw, id_raw = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        created_at = datetime.fromisoformat(created_raw)
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc)
        return created_at, uuid.UUID(id_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidInputError("Invalid cursor") from exc


# --- Use cases ---
async def save_message(session: AsyncSession, room_id: uuid.UUID, sender: User, content: str) -> MessageRead:
    await room_service.ensure_member(session, room_id, sender.id)
    message = Message(room_id=room_id, sender_id=sender.id, content=content, status=MessageStatus.SENT.value)
    session.add(message)
    await session.commit()
    return _to_read(message, sender.username)


async def get_history(
    session: AsyncSession,
    room_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None = None,
) -> MessagePage:
    """Return the newest ``limit`` messages older than ``cursor``, in chronological order.

    Keyset pagination on (created_at, id) is stable even when timestamps collide
    and stays fast on large rooms thanks to the composite index.
    """
    await room_service.ensure_member(session, room_id, user_id)

    query = (
        select(Message, User.username)
        .join(User, User.id == Message.sender_id)
        .where(Message.room_id == room_id)
    )
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                Message.created_at < cursor_time,
                and_(Message.created_at == cursor_time, Message.id < cursor_id),
            )
        )
    rows = (
        await session.execute(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1))
    ).all()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more:
        last_message = page_rows[-1][0]
        next_cursor = _encode_cursor(last_message.created_at, last_message.id)

    items = [_to_read(message, username) for message, username in reversed(page_rows)]
    return MessagePage(items=items, next_cursor=next_cursor, has_more=has_more)


async def acknowledge(
    session: AsyncSession, message_id: uuid.UUID, user_id: uuid.UUID, status: MessageStatus
) -> StatusChange | None:
    """Advance a message's delivery status (SENT -> DELIVERED -> READ, never backwards).

    Only room members other than the sender may acknowledge. Returns the change, or
    ``None`` when the status did not move forward.
    """
    message = await session.get(Message, message_id)
    if message is None:
        raise NotFoundError("Message not found")
    await room_service.ensure_member(session, message.room_id, user_id)
    if message.sender_id == user_id:
        raise PermissionDeniedError("Senders cannot acknowledge their own messages")

    if status.rank <= MessageStatus(message.status).rank:
        return None
    message.status = status.value
    await session.commit()
    return StatusChange(message_id=message.id, room_id=message.room_id, status=status)
