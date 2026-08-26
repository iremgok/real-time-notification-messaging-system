import time
import logging
from app.core.redis import redis_client
from app.core.config import settings

logger = logging.getLogger("app.rate_limiter")


class RateLimiter:
    @staticmethod
    async def is_rate_limited(
        user_id: str,
        limit: int = settings.WS_RATE_LIMIT_LIMIT,
        window: int = settings.WS_RATE_LIMIT_WINDOW,
        action: str = "ws_message",
    ) -> bool:
        """
        Implements a sliding window rate limiter using Redis Sorted Sets.
        Returns True if the request is rate-limited (blocked), False otherwise.
        """
        key = f"rate_limit:{user_id}:{action}"
        now = time.time()
        cutoff = now - window

        try:
            async with redis_client.pipeline(transaction=True) as pipe:
                # Remove requests older than the sliding window
                pipe.zremrangebyscore(key, 0, cutoff)
                # Add the current request with score and value as the timestamp
                # Using timestamp string value to guarantee uniqueness
                pipe.zadd(key, {f"{now}": now})
                # Get the number of elements in the window
                pipe.zcard(key)
                # Set dynamic TTL on the rate limit key
                pipe.expire(key, window + 10)

                # Execute pipeline
                results = await pipe.execute()

                # Results: [zremrangebyscore_count, zadd_count, zcard_count, expire_result]
                request_count = results[2]

                if request_count > limit:
                    logger.warning(
                        f"Rate limit exceeded for user {user_id} on action {action}. Count: {request_count}/{limit}"
                    )
                    return True
                return False

        except Exception as e:
            logger.error(f"Error checking rate limit in Redis: {e}")
            # Fail open or closed? Typically fail open in production for non-critical limiting,
            # but for DDoS protection we might want to log it. We fail open here.
            return False
