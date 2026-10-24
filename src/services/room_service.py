"""Room management: group rooms, 1:1 direct rooms and memberships."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import get_logger
from src.models.entities import Membership, Room, User
from src.services import auth_service
from src.services.exceptions import InvalidInputError, NotFoundError, PermissionDeniedError

logger = get_logger("rooms")


def _dm_key(a: uuid.UUID, b: uuid.UUID) -> str:
    first, second = sorted((str(a), str(b)))
    return f"{first}:{second}"


async def is_member(session: AsyncSession, room_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(Membership.user_id).where(Membership.room_id == room_id, Membership.user_id == user_id)
    )
    return found is not None


async def ensure_member(session: AsyncSession, room_id: uuid.UUID, user_id: uuid.UUID) -> None:
    if not await is_member(session, room_id, user_id):
        # Same error for "missing" and "forbidden" so room ids cannot be probed.
        raise PermissionDeniedError("You are not a member of this room")


async def list_room_ids(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.scalars(select(Membership.room_id).where(Membership.user_id == user_id))
    return list(rows)


async def list_rooms(session: AsyncSession, user_id: uuid.UUID) -> list[Room]:
    rows = await session.scalars(
        select(Room)
        .join(Membership, Membership.room_id == Room.id)
        .where(Membership.user_id == user_id)
        .order_by(Room.created_at.desc())
    )
    return list(rows)


async def create_group_room(session: AsyncSession, owner: User, name: str, description: str | None) -> Room:
    room = Room(name=name, description=description, is_group=True, created_by=owner.id)
    session.add(room)
    await session.flush()
    session.add(Membership(room_id=room.id, user_id=owner.id))
    await session.commit()
    logger.info("Group room created room_id=%s owner=%s", room.id, owner.id)
    return room


async def get_or_create_direct_room(session: AsyncSession, user: User, target_username: str) -> tuple[Room, bool]:
    """Return ``(room, created)``. Uniqueness is guaranteed by the ``dm_key`` constraint."""
    target = await auth_service.get_user_by_username(session, target_username)
    if target is None:
        raise NotFoundError("Target user not found")
    if target.id == user.id:
        raise InvalidInputError("Cannot open a direct conversation with yourself")

    key = _dm_key(user.id, target.id)
    existing = await session.scalar(select(Room).where(Room.dm_key == key))
    if existing is not None:
        return existing, False

    room = Room(name=f"{user.username} & {target.username}", is_group=False, dm_key=key, created_by=user.id)
    session.add(room)
    try:
        await session.flush()
        session.add_all([Membership(room_id=room.id, user_id=uid) for uid in (user.id, target.id)])
        await session.commit()
    except IntegrityError:
        # Another request created the same DM concurrently; return that one.
        await session.rollback()
        existing = await session.scalar(select(Room).where(Room.dm_key == key))
        if existing is None:
            raise
        return existing, False

    logger.info("Direct room created room_id=%s", room.id)
    return room, True


async def member_ids(session: AsyncSession, room_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.scalars(select(Membership.user_id).where(Membership.room_id == room_id))
    return list(rows)


async def join_group_room(session: AsyncSession, room_id: uuid.UUID, user: User) -> Room:
    room = await session.get(Room, room_id)
    if room is None:
        raise NotFoundError("Room not found")
    if not room.is_group:
        raise PermissionDeniedError("Direct conversations cannot be joined")
    if await is_member(session, room_id, user.id):
        return room

    session.add(Membership(room_id=room_id, user_id=user.id))
    try:
        await session.commit()
    except IntegrityError:  # concurrent double-join
        await session.rollback()
    logger.info("User joined room user_id=%s room_id=%s", user.id, room_id)
    return room


async def leave_room(session: AsyncSession, room_id: uuid.UUID, user: User) -> None:
    result = await session.execute(
        delete(Membership).where(Membership.room_id == room_id, Membership.user_id == user.id)
    )
    if result.rowcount == 0:
        raise NotFoundError("Membership not found")
    await session.commit()
    logger.info("User left room user_id=%s room_id=%s", user.id, room_id)
