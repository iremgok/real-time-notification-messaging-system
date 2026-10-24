"""A single client WebSocket with its own bounded outbound queue.

All writes go through one writer task, so the socket is never written to
concurrently, and a slow client can never block the Pub/Sub fan-out: when its
queue overflows it is disconnected instead.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, status

from src.core.logger import get_logger

logger = get_logger("ws.connection")


class ClientConnection:
    def __init__(
        self,
        websocket: WebSocket,
        user_id: uuid.UUID,
        username: str,
        token_expires_at: datetime,
        scope_room_id: uuid.UUID | None,
        queue_size: int,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.websocket = websocket
        self.user_id = user_id
        self.username = username
        self.token_expires_at = token_expires_at
        # None -> multiplexed session (all rooms); otherwise bound to one room.
        self.scope_room_id = scope_room_id
        self.rooms: set[uuid.UUID] = set()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._writer: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self.closed = False

    def accepts_room(self, room_id: uuid.UUID) -> bool:
        return self.scope_room_id is None or self.scope_room_id == room_id

    @property
    def token_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.token_expires_at

    def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop(), name=f"ws-writer-{self.id}")

    def send(self, payload: dict[str, Any]) -> None:
        """Queue a frame without blocking; disconnect clients that cannot keep up."""
        if self.closed:
            return
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("Send queue full, dropping slow client user_id=%s", self.user_id)
            self.close_soon(status.WS_1013_TRY_AGAIN_LATER, "Client too slow")

    def close_soon(self, code: int, reason: str) -> None:
        """Schedule a close from synchronous code (e.g. Pub/Sub fan-out)."""
        if self._close_task is None and not self.closed:
            self._close_task = asyncio.create_task(self.close(code, reason))

    async def close(self, code: int = status.WS_1000_NORMAL_CLOSURE, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        await self._drain(timeout=1.0)
        if self._writer is not None:
            self._writer.cancel()
        try:
            await self.websocket.close(code=code, reason=reason)
        except Exception:  # already closed by the peer
            pass

    async def _drain(self, timeout: float) -> None:
        """Give queued frames (e.g. a final error message) a chance to go out."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except (asyncio.TimeoutError, ValueError):
            pass

    async def _write_loop(self) -> None:
        try:
            while True:
                payload = await self._queue.get()
                try:
                    await self.websocket.send_text(json.dumps(payload, default=str))
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.info("Writer stopped for user_id=%s (peer gone)", self.user_id)
            self.closed = True
            # Unblock anyone waiting in _drain().
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
