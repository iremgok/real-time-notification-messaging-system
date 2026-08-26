import logging
import uuid
from datetime import datetime
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.room import Room
from app.models.membership import Membership
from app.models.message import Message
from app.schemas.chat import RoomCreate, RoomResponse, MessageResponse, MessageCursorPage
from app.services.connection_manager import manager

logger = logging.getLogger("app.chat")
router = APIRouter()


class DMCreateRequest(BaseModel):
    target_username: str = Field(..., description="Username of the user to start a DM with")


@router.post("/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    room_in: RoomCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Create a new chat room and add the creator as its first member.
    """
    try:
        new_room = Room(
            name=room_in.name or f"Group-{uuid.uuid4().hex[:6]}",
            description=room_in.description,
            is_group=room_in.is_group,
        )
        db.add(new_room)
        await db.flush()  # Obtain new_room.id

        membership = Membership(room_id=new_room.id, user_id=current_user.id)
        db.add(membership)
        await db.commit()
        await db.refresh(new_room)

        # Update local connection manager mapping for this user
        await manager.publish_message(
            f"user:{current_user.id}",
            {"type": "joined_room", "room_id": str(new_room.id)},
        )

        logger.info(f"Room created: ID={new_room.id} by User={current_user.id}")
        return new_room

    except Exception as e:
        logger.error(f"Error creating room: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create room",
        )


@router.post("/rooms/dm", response_model=RoomResponse, status_code=status.HTTP_200_OK)
async def get_or_create_dm_room(
    dm_in: DMCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Get or create a 1:1 Direct Message room between the current user and target user.
    """
    try:
        # Find target user
        stmt = select(User).where(User.username == dm_in.target_username)
        res = await db.execute(stmt)
        target_user = res.scalars().first()

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target user not found",
            )

        if target_user.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot start a Direct Message room with yourself",
            )

        # Check if a 1:1 room already exists between these two users
        # Query room IDs where both users are members and is_group is False
        stmt = (
            select(Room.id)
            .join(Membership, Room.id == Membership.room_id)
            .where(
                and_(
                    Room.is_group == False,
                    Membership.user_id.in_([current_user.id, target_user.id]),
                )
            )
            .group_by(Room.id)
            .having(func.count(Membership.user_id) == 2)
        )
        res = await db.execute(stmt)
        existing_room_id = res.scalars().first()

        if existing_room_id:
            # Fetch and return existing DM room
            stmt = select(Room).where(Room.id == existing_room_id)
            res = await db.execute(stmt)
            return res.scalars().first()

        # Create new 1:1 Room
        new_room = Room(
            name=f"DM: {current_user.username} & {target_user.username}",
            is_group=False,
        )
        db.add(new_room)
        await db.flush()

        # Add both users as members
        db.add(Membership(room_id=new_room.id, user_id=current_user.id))
        db.add(Membership(room_id=new_room.id, user_id=target_user.id))
        await db.commit()
        await db.refresh(new_room)

        # Notify both users' WebSockets to dynamically sync room membership
        await manager.publish_message(
            f"user:{current_user.id}",
            {"type": "joined_room", "room_id": str(new_room.id)},
        )
        await manager.publish_message(
            f"user:{target_user.id}",
            {"type": "joined_room", "room_id": str(new_room.id)},
        )

        logger.info(
            f"DM room created: ID={new_room.id} between {current_user.id} and {target_user.id}"
        )
        return new_room

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling DM room: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize direct message room",
        )


@router.get("/rooms", response_model=List[RoomResponse])
async def list_user_rooms(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    List all rooms the current user is a member of.
    """
    try:
        stmt = (
            select(Room)
            .join(Membership, Room.id == Membership.room_id)
            .where(Membership.user_id == current_user.id)
            .order_by(Room.created_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()
    except Exception as e:
        logger.error(f"Error listing user rooms: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve rooms",
        )


@router.post("/rooms/{room_id}/join", response_model=RoomResponse)
async def join_room(
    room_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Join an existing chat room.
    """
    try:
        stmt = select(Room).where(Room.id == room_id)
        result = await db.execute(stmt)
        room = result.scalars().first()

        if not room:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Room not found",
            )

        if not room.is_group:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot join a private 1:1 Direct Message room",
            )

        # Check if already a member
        stmt = select(Membership).where(
            and_(Membership.room_id == room_id, Membership.user_id == current_user.id)
        )
        result = await db.execute(stmt)
        if result.scalars().first():
            return room

        membership = Membership(room_id=room_id, user_id=current_user.id)
        db.add(membership)
        await db.commit()

        # Update local WebSocket routing
        await manager.publish_message(
            f"user:{current_user.id}",
            {"type": "joined_room", "room_id": str(room_id)},
        )

        logger.info(f"User={current_user.id} joined Room={room_id}")
        return room

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error joining room: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to join room",
        )


@router.post("/rooms/{room_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_room(
    room_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Leave a chat room.
    """
    try:
        stmt = select(Membership).where(
            and_(Membership.room_id == room_id, Membership.user_id == current_user.id)
        )
        result = await db.execute(stmt)
        membership = result.scalars().first()

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership in this room not found",
            )

        await db.delete(membership)
        await db.commit()

        # Notify manager to remove user from local room routing
        await manager.publish_message(
            f"user:{current_user.id}",
            {"type": "left_room", "room_id": str(room_id)},
        )

        logger.info(f"User={current_user.id} left Room={room_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error leaving room: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to leave room",
        )


@router.get("/rooms/{room_id}/messages", response_model=MessageCursorPage)
async def get_room_messages(
    room_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="ISO format datetime cursor string"),
):
    """
    Retrieve message history for a room using cursor-based pagination.
    """
    try:
        # Check if user is a member of the room
        stmt = select(Membership).where(
            and_(Membership.room_id == room_id, Membership.user_id == current_user.id)
        )
        result = await db.execute(stmt)
        if not result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a member of this room",
            )

        # Base query joining user to fetch sender username
        stmt = (
            select(Message, User.username)
            .join(User, Message.sender_id == User.id)
            .where(Message.room_id == room_id)
        )

        if cursor:
            try:
                cursor_dt = datetime.fromisoformat(cursor)
                stmt = stmt.where(Message.created_at < cursor_dt)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid cursor format. Must be ISO format timestamp.",
                )

        # Fetch limit + 1 to check if there is a next page
        stmt = stmt.order_by(Message.created_at.desc()).limit(limit + 1)
        result = await db.execute(stmt)
        rows = result.all()

        has_more = len(rows) > limit
        items_rows = rows[:limit] if has_more else rows

        # Map to schemas
        items = []
        for msg, username in items_rows:
            items.append(
                MessageResponse(
                    id=msg.id,
                    room_id=msg.room_id,
                    sender_id=msg.sender_id,
                    sender_username=username,
                    content=msg.content,
                    created_at=msg.created_at,
                    status=msg.status,
                )
            )

        # Oldest message in the fetched DESC list holds the next cursor timestamp
        next_cursor = None
        if has_more and items:
            next_cursor = items[-1].created_at.isoformat()

        # Reverse elements so client receives them oldest -> newest (chronological order)
        items.reverse()

        return MessageCursorPage(
            items=items, next_cursor=next_cursor, has_more=has_more
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching room messages: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve messages",
        )
