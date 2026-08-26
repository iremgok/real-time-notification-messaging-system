import json
import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.redis import redis_client
from app.core.config import settings
from app.models.membership import Membership

logger = logging.getLogger("app.presence")


class PresenceService:
    @staticmethod
    async def set_online(user_id: uuid.UUID, db: AsyncSession = None) -> None:
        """
        Mark a user as online in Redis and optionally broadcast this status
        to all rooms they are a member of.
        """
        key = f"presence:{user_id}"
        try:
            await redis_client.set(key, "online", ex=settings.PRESENCE_EXPIRE_SECONDS)
            logger.info(f"Presence: User {user_id} marked as online")

            if db:
                await PresenceService.broadcast_presence(user_id, "online", db)
        except Exception as e:
            logger.error(f"Error setting user {user_id} online: {e}")

    @staticmethod
    async def set_offline(user_id: uuid.UUID, db: AsyncSession = None) -> None:
        """
        Mark a user as offline in Redis (delete presence key) and optionally
        broadcast status.
        """
        key = f"presence:{user_id}"
        try:
            await redis_client.delete(key)
            logger.info(f"Presence: User {user_id} marked as offline")

            if db:
                await PresenceService.broadcast_presence(user_id, "offline", db)
        except Exception as e:
            logger.error(f"Error setting user {user_id} offline: {e}")

    @staticmethod
    async def get_presence(user_id: uuid.UUID) -> str:
        """
        Check if a user is online. Returns 'online' or 'offline'.
        """
        key = f"presence:{user_id}"
        try:
            val = await redis_client.get(key)
            return "online" if val else "offline"
        except Exception as e:
            logger.error(f"Error checking user {user_id} presence: {e}")
            return "offline"

    @staticmethod
    async def refresh_presence(user_id: uuid.UUID) -> None:
        """
        Refresh TTL of the online presence key in Redis.
        """
        key = f"presence:{user_id}"
        try:
            await redis_client.expire(key, settings.PRESENCE_EXPIRE_SECONDS)
        except Exception as e:
            logger.error(f"Error refreshing presence for user {user_id}: {e}")

    @staticmethod
    async def broadcast_presence(
        user_id: uuid.UUID, status: str, db: AsyncSession
    ) -> None:
        """
        Queries all rooms the user belongs to and publishes a presence change event
        to those Redis channels.
        """
        try:
            stmt = select(Membership.room_id).where(Membership.user_id == user_id)
            result = await db.execute(stmt)
            room_ids = result.scalars().all()

            presence_payload = json.dumps(
                {
                    "type": "presence_change",
                    "data": {"user_id": str(user_id), "status": status},
                }
            )

            for room_id in room_ids:
                channel = f"room:{room_id}"
                await redis_client.publish(channel, presence_payload)
                logger.debug(
                    f"Presence broadcast: published to {channel} for user {user_id}"
                )
        except Exception as e:
            logger.error(f"Error broadcasting presence for user {user_id}: {e}")
