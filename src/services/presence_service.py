"""Cluster-wide online presence using a per-user connection counter in Redis.

The counter is incremented per WebSocket (on any instance) and refreshed by activity,
so a crashed instance's stale counters expire after ``PRESENCE_TTL_SECONDS``.
"""

import uuid

from src.core.config import settings
from src.core.logger import get_logger
from src.services.redis_service import redis_service

logger = get_logger("presence")


def _key(user_id: uuid.UUID) -> str:
    return f"presence:{user_id}"


async def connection_opened(user_id: uuid.UUID) -> bool:
    """Return True when this is the user's first live connection (user became online)."""
    try:
        async with redis_service.client.pipeline(transaction=True) as pipe:
            pipe.incr(_key(user_id))
            pipe.expire(_key(user_id), settings.PRESENCE_TTL_SECONDS)
            count, _ = await pipe.execute()
        return count == 1
    except Exception:
        logger.exception("Failed to record presence for user_id=%s", user_id)
        return False


async def connection_closed(user_id: uuid.UUID) -> bool:
    """Return True when the user's last connection closed (user became offline)."""
    try:
        count = await redis_service.client.decr(_key(user_id))
        if count <= 0:
            await redis_service.client.delete(_key(user_id))
            return True
        return False
    except Exception:
        logger.exception("Failed to clear presence for user_id=%s", user_id)
        return False


async def refresh(user_id: uuid.UUID) -> None:
    try:
        await redis_service.client.expire(_key(user_id), settings.PRESENCE_TTL_SECONDS)
    except Exception:
        logger.debug("Presence refresh failed for user_id=%s", user_id)
