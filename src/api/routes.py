"""Aggregates the versioned REST API and exposes the health check."""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from src.api.dependencies import SessionDep
from src.api.endpoints import admin, auth, messages, rooms
from src.models.schemas import HealthRead
from src.services.redis_service import redis_service
from src.websockets.manager import manager

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(rooms.router)
api_router.include_router(messages.router)
api_router.include_router(admin.router)


@api_router.get("/health", response_model=HealthRead, tags=["health"])
async def health(session: SessionDep, response: Response) -> HealthRead:
    try:
        await session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False
    redis_ok = await redis_service.ping()

    healthy = database_ok and redis_ok
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthRead(
        status="ok" if healthy else "degraded",
        database=database_ok,
        redis=redis_ok,
        local_connections=manager.connection_count(),
    )
