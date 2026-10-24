"""WebSocket endpoints and the authenticated handshake.

* ``/ws``            multiplexed session: all of the user's rooms + notifications
* ``/ws/{room_id}``  session bound to one room (+ user notifications/broadcasts)

The JWT may be supplied (in order of preference) as:
1. ``Sec-WebSocket-Protocol: access_token, <jwt>``  (browsers; keeps it out of URLs/logs)
2. ``Authorization: Bearer <jwt>``                   (server-side clients)
3. ``?token=<jwt>``                                  (fallback; redacted from our logs)

Invalid credentials are rejected with close code 1008 (policy violation).
"""

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from src.core.config import settings
from src.core.logger import get_logger
from src.models.database import SessionFactory
from src.models.entities import User
from src.models.ws_schemas import ServerEvent, server_event
from src.services import auth_service, notification_service, presence_service, room_service
from src.services.auth_service import TokenClaims
from src.websockets.connection import ClientConnection
from src.websockets.handlers import CloseConnection, handle_frame
from src.websockets.manager import manager

logger = get_logger("ws.endpoints")
router = APIRouter()

TOKEN_SUBPROTOCOL = "access_token"


@router.websocket("/ws")
async def user_socket(websocket: WebSocket) -> None:
    await _serve(websocket, scope_room_id=None)


@router.websocket("/ws/{room_id}")
async def room_socket(websocket: WebSocket, room_id: uuid.UUID) -> None:
    await _serve(websocket, scope_room_id=room_id)


# --- Handshake helpers ---
def _extract_token(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Return ``(token, subprotocol_to_accept)``."""
    offered = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    if len(offered) >= 2 and offered[0] == TOKEN_SUBPROTOCOL:
        return offered[1], TOKEN_SUBPROTOCOL

    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None, None

    return websocket.query_params.get("token"), None


def _origin_allowed(websocket: WebSocket) -> bool:
    allowed = settings.ws_allowed_origins
    origin = websocket.headers.get("origin")
    # Non-browser clients send no Origin; browsers always do (protects against CSWSH).
    return not allowed or origin is None or origin in allowed


async def _reject(websocket: WebSocket, subprotocol: str | None, reason: str) -> None:
    # Accept first so the client actually receives the 1008 close code and reason
    # (closing before accept turns into a bare HTTP 403).
    await websocket.accept(subprotocol=subprotocol)
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=reason)
    logger.info("WebSocket rejected: %s", reason)


async def _authorize(
    websocket: WebSocket, token: str | None, scope_room_id: uuid.UUID | None
) -> tuple[User, TokenClaims, list[uuid.UUID]] | str:
    """Return the authenticated context, or a rejection reason."""
    if not _origin_allowed(websocket):
        return "Origin not allowed"
    if not token:
        return "Authentication required"

    async with SessionFactory() as session:
        resolved = await auth_service.resolve_token(session, token)
        if resolved is None:
            return "Invalid or expired token"
        user, claims = resolved
        room_ids = await room_service.list_room_ids(session, user.id)

    if scope_room_id is not None and scope_room_id not in room_ids:
        return "Not a member of this room"
    if manager.connection_count(user.id) >= settings.WS_MAX_CONNECTIONS_PER_USER:
        return "Too many connections"
    return user, claims, room_ids


# --- Session lifecycle ---
async def _serve(websocket: WebSocket, scope_room_id: uuid.UUID | None) -> None:
    token, subprotocol = _extract_token(websocket)
    outcome = await _authorize(websocket, token, scope_room_id)
    if isinstance(outcome, str):
        await _reject(websocket, subprotocol, outcome)
        return
    user, claims, room_ids = outcome

    await websocket.accept(subprotocol=subprotocol)
    conn = ClientConnection(
        websocket=websocket,
        user_id=user.id,
        username=user.username,
        token_expires_at=claims.expires_at,
        scope_room_id=scope_room_id,
        queue_size=settings.WS_SEND_QUEUE_SIZE,
    )
    conn.start()
    manager.register(conn, room_ids)
    conn.send(
        server_event(
            ServerEvent.CONNECTED,
            {"user_id": str(user.id), "username": user.username, "rooms": [str(r) for r in conn.rooms]},
        )
    )
    if await presence_service.connection_opened(user.id):
        await notification_service.presence_changed(user.id, room_ids, online=True)

    try:
        await _receive_loop(conn)
    finally:
        manager.unregister(conn)
        await conn.close()
        if await presence_service.connection_closed(user.id):
            # Membership may have changed during the session; re-read it.
            async with SessionFactory() as session:
                current_rooms = await room_service.list_room_ids(session, user.id)
            await notification_service.presence_changed(user.id, current_rooms, online=False)


async def _receive_loop(conn: ClientConnection) -> None:
    try:
        while not conn.closed:
            frame = await conn.websocket.receive()
            if frame["type"] == "websocket.disconnect":
                return
            text = frame.get("text")
            if text is None:
                await conn.close(status.WS_1003_UNSUPPORTED_DATA, "Only JSON text frames are supported")
                return
            await handle_frame(conn, text)
    except CloseConnection as exc:
        await conn.close(exc.code, exc.reason)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # Starlette raises RuntimeError when receiving on a socket we already closed.
        pass
    except Exception:
        logger.exception("Unexpected error in WebSocket session user_id=%s", conn.user_id)
        await conn.close(status.WS_1011_INTERNAL_ERROR, "Internal error")
