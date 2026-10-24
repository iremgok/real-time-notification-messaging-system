"""Distributed fixed-window rate limiter backed by Redis (shared by all instances)."""

import time

from src.core.config import settings
from src.core.logger import get_logger
from src.services.redis_service import RedisService, redis_service

logger = get_logger("rate_limit")


class RateLimiter:
    def __init__(self, redis: RedisService, namespace: str, limit: int, window_seconds: int) -> None:
        self._redis = redis
        self._namespace = namespace
        self.limit = limit
        self.window = window_seconds

    async def allow(self, identity: str) -> bool:
        """Count one hit for ``identity``; return False when the window budget is exhausted.

        Fails open (allows) if Redis is unavailable so an outage of the limiter does
        not take messaging down; the error is logged for alerting.
        """
        window_index = int(time.time()) // self.window
        key = f"ratelimit:{self._namespace}:{identity}:{window_index}"
        try:
            async with self._redis.client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, self.window + 1)
                count, _ = await pipe.execute()
        except Exception:
            logger.exception("Rate limiter unavailable (namespace=%s); failing open", self._namespace)
            return True

        if count > self.limit:
            if count == self.limit + 1:  # log once per window, not on every rejected hit
                logger.warning("Rate limit exceeded namespace=%s identity=%s", self._namespace, identity)
            return False
        return True


ws_message_limiter = RateLimiter(
    redis_service, "ws", settings.WS_RATE_LIMIT_COUNT, settings.WS_RATE_LIMIT_WINDOW_SECONDS
)
auth_limiter = RateLimiter(
    redis_service, "auth", settings.AUTH_RATE_LIMIT_COUNT, settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
)
