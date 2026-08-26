import asyncio
import json
import logging
import uuid
from typing import Dict, Set
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.redis import redis_client
from app.models.membership import Membership

logger = logging.getLogger("app.connection_manager")


class ConnectionManager:
    def __init__(self):
        # Maps user_id -> WebSocket connection
        self.active_connections: Dict[uuid.UUID, WebSocket] = {}

        # Maps room_id -> Set of local user_ids connected to this instance
        self.room_members: Dict[uuid.UUID, Set[uuid.UUID]] = {}

        # Background task for Redis Pub/Sub
        self.pubsub_task: getattr(asyncio, "Task", None) = None

    async def connect(
        self, user_id: uuid.UUID, websocket: WebSocket, db: AsyncSession
    ) -> None:
        """
        Accept WebSocket connection, add to active connections, and load room memberships.
        """
        self.active_connections[user_id] = websocket

        # Query all rooms the user is a member of
        try:
            stmt = select(Membership.room_id).where(Membership.user_id == user_id)
            result = await db.execute(stmt)
            room_ids = result.scalars().all()

            for room_id in room_ids:
                if room_id not in self.room_members:
                    self.room_members[room_id] = set()
                self.room_members[room_id].add(user_id)

            logger.info(
                f"WebSocket: User {user_id} connected locally. Subscribed to {len(room_ids)} rooms."
            )
        except Exception as e:
            logger.error(f"Error loading memberships for user {user_id} on connect: {e}")

    async def disconnect(self, user_id: uuid.UUID) -> None:
        """
        Remove user from active connections and room tracking.
        """
        if user_id in self.active_connections:
            del self.active_connections[user_id]

        # Clean user from all room trackers
        rooms_to_cleanup = []
        for room_id, members in self.room_members.items():
            if user_id in members:
                members.remove(user_id)
                if not members:
                    rooms_to_cleanup.append(room_id)

        # Cleanup memory for empty rooms
        for room_id in rooms_to_cleanup:
            del self.room_members[room_id]

        logger.info(f"WebSocket: User {user_id} disconnected locally.")

    async def send_local_message(self, user_id: uuid.UUID, message: dict) -> None:
        """
        Send a message directly to a locally connected WebSocket.
        """
        websocket = self.active_connections.get(user_id)
        if websocket:
            try:
                await websocket.send_text(json.dumps(message, default=str))
            except Exception as e:
                logger.warning(
                    f"Failed to send message to user {user_id}. Disconnecting connection. Error: {e}"
                )
                await self.disconnect(user_id)

    async def publish_message(self, channel: str, message: dict) -> None:
        """
        Publish a message to Redis Pub/Sub.
        """
        try:
            await redis_client.publish(channel, json.dumps(message, default=str))
        except Exception as e:
            logger.error(f"Failed to publish message to channel {channel}: {e}")

    async def listen_to_redis_pubsub(self) -> None:
        """
        Continuous loop listening to Redis Pub/Sub channels and routing messages
        to local WebSocket clients. Runs in a background task.
        """
        pubsub = redis_client.pubsub()
        try:
            # Pattern subscribe to match user-specific, room-specific, and global broadcasts
            await pubsub.psubscribe("user:*", "room:*", "broadcast:*")
            logger.info("Started Redis Pub/Sub listener background task.")

            async for message in pubsub.listen():
                if message["type"] != "pmessage":
                    continue

                channel = message["channel"].decode("utf-8")
                data_str = message["data"].decode("utf-8")

                try:
                    payload = json.loads(data_str)
                except Exception as e:
                    logger.error(f"Failed to parse JSON from Redis message: {e}")
                    continue

                # Route the message based on channel pattern
                if channel.startswith("user:"):
                    user_id_str = channel.split("user:")[1]
                    try:
                        user_id = uuid.UUID(user_id_str)
                    except ValueError:
                        continue

                    # Handle connection-specific synchronization events
                    # (e.g. dynamic joining/leaving rooms from REST operations)
                    msg_type = payload.get("type")
                    if msg_type == "joined_room":
                        try:
                            room_id = uuid.UUID(payload.get("room_id"))
                            if room_id not in self.room_members:
                                self.room_members[room_id] = set()
                            self.room_members[room_id].add(user_id)
                            logger.info(f"Dynamically added User {user_id} to Room {room_id} mappings")
                        except Exception as e:
                            logger.error(f"Error handling dynamic joined_room: {e}")
                    elif msg_type == "left_room":
                        try:
                            room_id = uuid.UUID(payload.get("room_id"))
                            if room_id in self.room_members and user_id in self.room_members[room_id]:
                                self.room_members[room_id].remove(user_id)
                                if not self.room_members[room_id]:
                                    del self.room_members[room_id]
                                logger.info(f"Dynamically removed User {user_id} from Room {room_id} mappings")
                        except Exception as e:
                            logger.error(f"Error handling dynamic left_room: {e}")

                    # Forward message to user
                    await self.send_local_message(user_id, payload)

                elif channel.startswith("room:"):
                    room_id_str = channel.split("room:")[1]
                    try:
                        room_id = uuid.UUID(room_id_str)
                    except ValueError:
                        continue

                    # Send to all local members in this room
                    if room_id in self.room_members:
                        # Copy set to avoid size change during iteration if disconnects happen
                        local_members = list(self.room_members[room_id])
                        for member_id in local_members:
                            await self.send_local_message(member_id, payload)

                elif channel == "broadcast:global":
                    # Send to all active connections on this server instance
                    local_users = list(self.active_connections.keys())
                    for user_id in local_users:
                        await self.send_local_message(user_id, payload)

        except asyncio.CancelledError:
            logger.info("Redis Pub/Sub listener task is being cancelled.")
        except Exception as e:
            logger.critical(f"Redis Pub/Sub listener task encountered an error: {e}")
        finally:
            await pubsub.punsubscribe()
            await pubsub.close()
            logger.info("Redis Pub/Sub listener shut down.")

    def start_pubsub_listener(self) -> None:
        """
        Spawn the background listener task.
        """
        self.pubsub_task = asyncio.create_task(self.listen_to_redis_pubsub())

    def stop_pubsub_listener(self) -> None:
        """
        Cancel the background listener task.
        """
        if self.pubsub_task:
            self.pubsub_task.cancel()


# Instantiate a singleton ConnectionManager
manager = ConnectionManager()
