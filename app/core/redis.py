import logging
from redis.asyncio import Redis, from_url
from app.core.config import settings

logger = logging.getLogger("app.redis")

# Global async Redis client
redis_client: Redis = from_url(settings.REDIS_URL)


async def init_redis() -> None:
    """
    Initialize Redis connection and test connection with a Ping.
    """
    try:
        await redis_client.ping()
        logger.info("Successfully connected to Redis.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise e


async def close_redis() -> None:
    """
    Close Redis client connection.
    """
    await redis_client.close()
    logger.info("Redis connection closed.")
