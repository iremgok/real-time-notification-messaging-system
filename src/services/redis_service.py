"""Redis client lifecycle and the Pub/Sub bridge between application instances.

Every instance publishes events to namespaced channels and pattern-subscribes to all
of them. The local ``ConnectionManager`` (registered as the handler) then fans out
each event only to the WebSockets connected to *this* instance. This lets any number
of uvicorn workers / containers serve the same rooms.
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from src.core.config import settings
from src.core.logger import get_logger

logger = get_logger("redis")

CHANNEL_PREFIX = "chat:"
ROOM_PREFIX = f"{CHANNEL_PREFIX}room:"
USER_PREFIX = f"{CHANNEL_PREFIX}user:"
BROADCAST_CHANNEL = f"{CHANNEL_PREFIX}broadcast"

PubSubHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


def room_channel(room_id: uuid.UUID) -> str:
    return f"{ROOM_PREFIX}{room_id}"


def user_channel(user_id: uuid.UUID) -> str:
    return f"{USER_PREFIX}{user_id}"


class RedisService:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Redis | None = None
        self._listener: asyncio.Task[None] | None = None
        # Overridable for tests (e.g. fakeredis).
        self.client_factory: Callable[[], Redis] = lambda: Redis.from_url(
            self._url, decode_responses=True, health_check_interval=30
        )

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("Redis is not connected; call connect() during startup")
        return self._client

    async def connect(self) -> None:
        self._client = self.client_factory()
        await self._client.ping()
        logger.info("Connected to Redis")

    async def close(self) -> None:
        await self.stop_listener()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("Redis connection closed")

    async def ping(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            return False

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        await self.client.publish(channel, json.dumps(payload, default=str))

    # --- Pub/Sub listener ---
    def start_listener(self, handler: PubSubHandler) -> None:
        if self._listener is None or self._listener.done():
            self._listener = asyncio.create_task(self._listen_forever(handler), name="redis-pubsub")

    async def stop_listener(self) -> None:
        if self._listener is None:
            return
        self._listener.cancel()
        try:
            await self._listener
        except asyncio.CancelledError:
            pass
        self._listener = None

    async def _listen_forever(self, handler: PubSubHandler) -> None:
        """Keep the subscription alive; reconnect with exponential backoff on failures."""
        backoff = 1.0
        while True:
            pubsub = self.client.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
                logger.info("Pub/Sub listener subscribed")
                backoff = 1.0
                async for message in pubsub.listen():
                    if message.get("type") == "pmessage":
                        await self._dispatch(handler, message["channel"], message["data"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pub/Sub listener failed; retrying in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass

    @staticmethod
    async def _dispatch(handler: PubSubHandler, channel: str, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Dropped malformed Pub/Sub payload on %s", channel)
            return
        try:
            await handler(channel, payload)
        except Exception:  # one bad event must never kill the listener
            logger.exception("Pub/Sub handler failed for channel %s", channel)


redis_service = RedisService(settings.REDIS_URL)
