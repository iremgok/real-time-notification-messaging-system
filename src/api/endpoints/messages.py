"""/messages: paginated message history of a room."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from src.api.dependencies import CurrentUser, SessionDep
from src.models.schemas import MessagePage
from src.services import chat_service

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("/{room_id}", response_model=MessagePage)
async def get_room_history(
    room_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200, description="`next_cursor` of the previous page")] = None,
) -> MessagePage:
    """Newest messages first page; follow `next_cursor` to go back in time.

    Items inside a page are ordered oldest -> newest.
    """
    return await chat_service.get_history(session, room_id, user.id, limit, cursor)
