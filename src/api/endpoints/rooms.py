"""/rooms: group rooms, 1:1 direct rooms and membership."""

import uuid

from fastapi import APIRouter, Response, status

from src.api.dependencies import CurrentUser, SessionDep
from src.models.entities import Room
from src.models.schemas import DirectRoomCreate, RoomCreate, RoomRead
from src.services import notification_service, room_service

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.get("", response_model=list[RoomRead])
async def list_rooms(user: CurrentUser, session: SessionDep) -> list[Room]:
    return await room_service.list_rooms(session, user.id)


@router.post("", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
async def create_room(payload: RoomCreate, user: CurrentUser, session: SessionDep) -> Room:
    room = await room_service.create_group_room(session, user, payload.name, payload.description)
    await notification_service.membership_changed(user.id, room.id, joined=True)
    return room


@router.post("/direct", response_model=RoomRead)
async def open_direct_room(
    payload: DirectRoomCreate, user: CurrentUser, session: SessionDep, response: Response
) -> Room:
    room, created = await room_service.get_or_create_direct_room(session, user, payload.target_username)
    if created:
        response.status_code = status.HTTP_201_CREATED
        for member_id in await room_service.member_ids(session, room.id):
            await notification_service.membership_changed(member_id, room.id, joined=True)
    return room


@router.post("/{room_id}/join", response_model=RoomRead)
async def join_room(room_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> Room:
    room = await room_service.join_group_room(session, room_id, user)
    await notification_service.membership_changed(user.id, room.id, joined=True)
    return room


@router.post("/{room_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_room(room_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    await room_service.leave_room(session, room_id, user)
    await notification_service.membership_changed(user.id, room_id, joined=False)
