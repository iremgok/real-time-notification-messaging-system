"""/admin: system-wide and targeted notifications (admin only)."""

import uuid

from fastapi import APIRouter, status

from src.api.dependencies import AdminUser, SessionDep
from src.core.logger import get_logger
from src.models.schemas import NotificationCreate
from src.services import auth_service, notification_service
from src.services.exceptions import NotFoundError

logger = get_logger("admin")
router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/broadcast", status_code=status.HTTP_202_ACCEPTED)
async def broadcast(payload: NotificationCreate, admin: AdminUser) -> dict[str, str]:
    await notification_service.broadcast(payload, sender=admin.username)
    logger.info("Admin broadcast sent by user_id=%s level=%s", admin.id, payload.level)
    return {"status": "queued"}


@router.post("/notify/{user_id}", status_code=status.HTTP_202_ACCEPTED)
async def notify_user(
    user_id: uuid.UUID, payload: NotificationCreate, admin: AdminUser, session: SessionDep
) -> dict[str, str]:
    if await auth_service.get_active_user(session, user_id) is None:
        raise NotFoundError("User not found")
    await notification_service.notify_user(user_id, payload, sender=admin.username)
    logger.info("Admin notification sent by user_id=%s to user_id=%s", admin.id, user_id)
    return {"status": "queued"}
