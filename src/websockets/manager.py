"""Local (per-instance) connection pool, room index and event fan-out.

The manager never talks to other instances directly: it receives every event via
the Redis Pub/Sub bridge (``dispatch``) and delivers it to the sockets connected
here. A user may hold several sockets (tabs/devices), each multiplexed over all
of their rooms (``/ws``) or bound to a single room (``/ws/{room_id}``).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from fastapi import status

from src.core.logger import get_logger
from src.models.ws_schemas import ServerEvent
from src.services.redis_service import BROADCAST_CHANNEL, ROOM_PREFIX, USER_PREFIX
from src.websockets.connection import ClientConnection

logger = get_logger("ws.manager")


class ConnectionManager:
    def __init__(self) -> None:
        self._by_user: dict[uuid.UUID, set[ClientConnection]] = defaultdict(set)
        self._by_room: dict[uuid.UUID, set[ClientConnection]] = defaultdict(set)

    # --- Registry ---
    def connection_count(self, user_id: uuid.UUID | None = None) -> int:
        if user_id is not None:
            return len(self._by_user.get(user_id, ()))
        return sum(len(conns) for conns in self._by_user.values())

    def register(self, conn: ClientConnection, room_ids: Iterable[uuid.UUID]) -> None:
        self._by_user[conn.user_id].add(conn)
        for room_id in room_ids:
            if conn.accepts_room(room_id):
                self._subscribe(conn, room_id)
        logger.info("Connected user_id=%s rooms=%d local_total=%d", conn.user_id, len(conn.rooms), self.connection_count())

    def unregister(self, conn: ClientConnection) -> None:
        for room_id in list(conn.rooms):
            self._unsubscribe(conn, room_id)
        user_conns = self._by_user.get(conn.user_id)
        if user_conns is not None:
            user_conns.discard(conn)
            if not user_conns:
                del self._by_user[conn.user_id]
        logger.info("Disconnected user_id=%s local_total=%d", conn.user_id, self.connection_count())

    def _subscribe(self, conn: ClientConnection, room_id: uuid.UUID) -> None:
        conn.rooms.add(room_id)
        self._by_room[room_id].add(conn)

    def _unsubscribe(self, conn: ClientConnection, room_id: uuid.UUID) -> None:
        conn.rooms.discard(room_id)
        members = self._by_room.get(room_id)
        if members is not None:
            members.discard(conn)
            if not members:
                del self._by_room[room_id]

    # --- Fan-out (called by the Redis Pub/Sub listener) ---
    async def dispatch(self, channel: str, payload: dict[str, Any]) -> None:
        if channel == BROADCAST_CHANNEL:
            self._send_all(payload)
        elif channel.startswith(ROOM_PREFIX):
            room_id = _parse_uuid(channel[len(ROOM_PREFIX):])
            if room_id is not None:
                self._send_many(self._by_room.get(room_id, ()), payload)
        elif channel.startswith(USER_PREFIX):
            user_id = _parse_uuid(channel[len(USER_PREFIX):])
            if user_id is not None:
                self._deliver_to_user(user_id, payload)

    def _deliver_to_user(self, user_id: uuid.UUID, payload: dict[str, Any]) -> None:
        conns = list(self._by_user.get(user_id, ()))
        if not conns:
            return
        event_type = payload.get("type")
        room_id = _parse_uuid(str(payload.get("data", {}).get("room_id", "")))

        for conn in conns:
            if room_id is not None and event_type == ServerEvent.JOINED_ROOM.value and conn.accepts_room(room_id):
                self._subscribe(conn, room_id)
            elif room_id is not None and event_type == ServerEvent.LEFT_ROOM.value:
                self._unsubscribe(conn, room_id)
                if conn.scope_room_id == room_id:
                    conn.send(payload)
                    conn.close_soon(status.WS_1008_POLICY_VIOLATION, "Membership revoked")
                    continue
            conn.send(payload)

    def _send_all(self, payload: dict[str, Any]) -> None:
        for conns in list(self._by_user.values()):
            self._send_many(conns, payload)

    @staticmethod
    def _send_many(conns: Iterable[ClientConnection], payload: dict[str, Any]) -> None:
        for conn in list(conns):  # copy: a send may trigger a close/unregister
            conn.send(payload)

    async def close_all(self) -> None:
        """Graceful shutdown: ask clients to reconnect (they will land on a live instance)."""
        for conns in list(self._by_user.values()):
            for conn in list(conns):
                await conn.close(status.WS_1012_SERVICE_RESTART, "Server restarting")


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


manager = ConnectionManager()
