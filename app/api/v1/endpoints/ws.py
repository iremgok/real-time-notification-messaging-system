import json
import logging
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select, and_

from app.core.security import decode_access_token
from app.core.config import settings
from app.db.session import async_session, get_db
from app.models.user import User
from app.models.room import Room
from app.models.membership import Membership
from app.models.message import Message
from app.services.connection_manager import manager
from app.services.presence import PresenceService
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger("app.ws")
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint handling auth, rate-limiting, pings,
    payload enforcement, persistence, and real-time message routing.
    """
    # 1. Authenticate WebSocket Handshake
    token = websocket.query_params.get("token")
    accept_subprotocol = None

    if not token:
        # Fallback to subprotocol headers
        subprotocols_header = websocket.headers.get("sec-websocket-protocol")
        if subprotocols_header:
            parts = [p.strip() for p in subprotocols_header.split(",")]
            # Format expected: ["access_token", "TOKEN_STRING"]
            if len(parts) > 1:
                token = parts[1]
                accept_subprotocol = parts[0]
            else:
                token = parts[0]
                accept_subprotocol = "access_token"

    if not token:
        logger.warning("WebSocket connection rejected: Token missing")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Authentication token missing"
        )
        return

    # Verify and decode token
    user_id_str = decode_access_token(token)
    if not user_id_str:
        logger.warning("WebSocket connection rejected: Invalid or expired token")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token"
        )
        return

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        logger.warning(f"WebSocket connection rejected: Invalid user ID format: {user_id_str}")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Invalid user ID format"
        )
        return

    # 2. Establish connection and initialize states
    # We open a scoped session to setup connection
    async with async_session() as db:
        # Check if user actually exists
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalars().first()
        if not user:
            logger.warning(f"WebSocket connection rejected: User {user_id} not found")
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="User not found"
            )
            return

        # Add to local connection manager
        await manager.connect(user_id, websocket, db)
        # Mark online and broadcast presence
        await PresenceService.set_online(user_id, db)

    # If subprotocols were requested, return the accepted one during handshake
    await websocket.accept(subprotocol=accept_subprotocol)

    try:
        # Receive loop
        while True:
            # Enforce max payload size at frame receipt
            data_text = await websocket.receive_text()
            payload_size = len(data_text.encode("utf-8"))

            if payload_size > settings.WS_MAX_MESSAGE_SIZE:
                logger.warning(
                    f"User {user_id} sent payload exceeding limit: {payload_size} bytes"
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"message": "Payload size exceeds maximum allowed size."},
                    }
                )
                # Violators of message sizes are disconnected
                await websocket.close(
                    code=status.WS_1009_MESSAGE_TOO_BIG,
                    reason="Message payload too large",
                )
                break

            # Parse frame payload
            try:
                event = json.loads(data_text)
            except Exception as e:
                logger.warning(f"Invalid JSON received from user {user_id}: {e}")
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Invalid JSON frame"}}
                )
                continue

            event_type = event.get("type")
            if not event_type:
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Missing event type"}}
                )
                continue

            # 3. Rate Limit Enforcement (per WebSocket action/message)
            is_limited = await RateLimiter.is_rate_limited(
                str(user_id), action="ws_message"
            )
            if is_limited:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"message": "Rate limit exceeded. Please slow down."},
                    }
                )
                continue

            # Refresh presence on activity
            await PresenceService.refresh_presence(user_id)

            # 4. Route events
            if event_type == "ping":
                # Heartbeat handler
                await websocket.send_json({"type": "pong"})

            elif event_type == "send_message":
                data = event.get("data", {})
                room_id_str = data.get("room_id")
                content = data.get("content")

                if not room_id_str or not content:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "data": {"message": "Missing room_id or content"},
                        }
                    )
                    continue

                try:
                    room_id = uuid.UUID(room_id_str)
                except ValueError:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "data": {"message": "Invalid room_id format"},
                        }
                    )
                    continue

                # Process message sending using a dedicated DB transaction
                async with async_session() as db:
                    # Verify membership
                    stmt = select(Membership).where(
                        and_(
                            Membership.room_id == room_id,
                            Membership.user_id == user_id,
                        )
                    )
                    res = await db.execute(stmt)
                    membership = res.scalars().first()

                    if not membership:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "data": {
                                    "message": "Forbidden: You are not a member of this room"
                                },
                            }
                        )
                        continue

                    # Save to DB
                    new_msg = Message(
                        room_id=room_id,
                        sender_id=user_id,
                        content=content,
                        status="SENT",
                    )
                    db.add(new_msg)
                    await db.commit()
                    await db.refresh(new_msg)

                    # Query sender's username for UI inclusion
                    stmt = select(User.username).where(User.id == user_id)
                    username_res = await db.execute(stmt)
                    sender_username = username_res.scalar() or "Unknown"

                # Publish message to Redis Pub/Sub room channel
                # All instances (including this one) will receive and route it to connected members
                msg_payload = {
                    "type": "message",
                    "data": {
                        "id": str(new_msg.id),
                        "room_id": str(new_msg.room_id),
                        "sender_id": str(new_msg.sender_id),
                        "sender_username": sender_username,
                        "content": new_msg.content,
                        "created_at": new_msg.created_at.isoformat(),
                        "status": new_msg.status,
                    },
                }
                await manager.publish_message(f"room:{room_id}", msg_payload)

            elif event_type == "message_ack":
                data = event.get("data", {})
                message_id_str = data.get("message_id")
                new_status = data.get("status")  # DELIVERED or READ

                if not message_id_str or new_status not in ["DELIVERED", "READ"]:
                    continue

                try:
                    message_id = uuid.UUID(message_id_str)
                except ValueError:
                    continue

                # Update message status in database
                async with async_session() as db:
                    stmt = select(Message).where(Message.id == message_id)
                    res = await db.execute(stmt)
                    db_message = res.scalars().first()

                    if db_message:
                        # Update status (only progress forward: SENT -> DELIVERED -> READ)
                        status_hierarchy = {"SENT": 1, "DELIVERED": 2, "READ": 3}
                        curr_rank = status_hierarchy.get(db_message.status, 0)
                        new_rank = status_hierarchy.get(new_status, 0)

                        if new_rank > curr_rank:
                            db_message.status = new_status
                            await db.commit()

                            # Notify room members of the status update
                            status_payload = {
                                "type": "message_status_update",
                                "data": {
                                    "message_id": str(message_id),
                                    "room_id": str(db_message.room_id),
                                    "status": new_status,
                                },
                            }
                            await manager.publish_message(
                                f"room:{db_message.room_id}", status_payload
                            )

            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"message": f"Unsupported event type: {event_type}"},
                    }
                )

    except WebSocketDisconnect:
        logger.info(f"WebSocket: Connection disconnected for User {user_id}")
    except Exception as e:
        logger.error(f"WebSocket: Exception in session for User {user_id}: {e}")
    finally:
        # 5. Clean up presence and connection trackers
        await manager.disconnect(user_id)
        # We need a db session to broadcast offline presence
        async with async_session() as db:
            await PresenceService.set_offline(user_id, db)
