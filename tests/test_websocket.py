import pytest
from fastapi import status
from sqlalchemy import update
from starlette.websockets import WebSocketDisconnect

from src.models.database import SessionFactory
from src.models.entities import User


def _receive_until(ws, event_type, limit=20):
    for _ in range(limit):
        frame = ws.receive_json()
        if frame["type"] == event_type:
            return frame
    raise AssertionError(f"{event_type} not received")


def _auth(token):
    return ["access_token", token]


def test_rejects_invalid_token_with_1008(client):
    with client.websocket_connect("/ws?token=invalid") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == status.WS_1008_POLICY_VIOLATION


def test_rejects_non_member_room_socket(client, make_user):
    owner, stranger = make_user(), make_user()
    room = client.post("/api/v1/rooms", json={"name": "private"}, headers=owner["headers"]).json()
    with client.websocket_connect(f"/ws/{room['id']}", subprotocols=_auth(stranger["token"])) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == status.WS_1008_POLICY_VIOLATION


def test_message_roundtrip_persistence_and_ack(client, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    room = client.post(
        "/api/v1/rooms/direct", json={"target_username": bob["username"]}, headers=alice["headers"]
    ).json()

    with client.websocket_connect("/ws", subprotocols=_auth(alice["token"])) as alice_ws:
        with client.websocket_connect(f"/ws/{room['id']}", headers=bob["headers"]) as bob_ws:
            _receive_until(alice_ws, "connected")
            _receive_until(bob_ws, "connected")

            alice_ws.send_json(
                {"type": "send_message", "data": {"room_id": room["id"], "content": " hello ", "client_id": "c1"}}
            )
            assert _receive_until(alice_ws, "message_sent")["data"]["client_id"] == "c1"
            message = _receive_until(bob_ws, "message")["data"]
            assert message["content"] == "hello" and message["sender_username"] == alice["username"]

            bob_ws.send_json({"type": "message_ack", "data": {"message_id": message["id"], "status": "READ"}})
            update_event = _receive_until(alice_ws, "message_status_update")["data"]
            assert update_event == {"message_id": message["id"], "room_id": room["id"], "status": "READ"}

    history = client.get(f"/api/v1/messages/{room['id']}", headers=bob["headers"]).json()
    assert [m["content"] for m in history["items"]] == ["hello"]
    assert history["items"][0]["status"] == "READ"


def test_validation_rate_limit_and_size(client, make_user):
    user = make_user()
    room = client.post("/api/v1/rooms", json={"name": "spam"}, headers=user["headers"]).json()
    with client.websocket_connect(f"/ws/{room['id']}", subprotocols=_auth(user["token"])) as ws:
        _receive_until(ws, "connected")
        ws.send_json({"type": "send_message", "data": {"content": ""}})
        assert "content" in _receive_until(ws, "error")["data"]["message"]

        # WS_RATE_LIMIT_COUNT=5 in tests: at least one of these must be throttled
        # (even if a window boundary is crossed in between).
        for _ in range(12):
            ws.send_json({"type": "ping"})
        frames = [ws.receive_json() for _ in range(12)]
        assert any(f["type"] == "error" and "Rate limit" in f["data"]["message"] for f in frames)

        ws.send_text("x" * 20000)
        with pytest.raises(WebSocketDisconnect) as exc:
            _receive_until(ws, "never")
        assert exc.value.code == status.WS_1009_MESSAGE_TOO_BIG


def test_admin_broadcast_reaches_sockets(client, make_user):
    admin, listener = make_user("admin"), make_user("listener")

    async def promote():
        async with SessionFactory() as session:
            await session.execute(update(User).where(User.username == admin["username"]).values(is_admin=True))
            await session.commit()

    client.portal.call(promote)

    with client.websocket_connect("/ws", subprotocols=_auth(listener["token"])) as ws:
        _receive_until(ws, "connected")
        response = client.post(
            "/api/v1/admin/broadcast",
            json={"title": "Maintenance", "body": "Tonight", "level": "warning"},
            headers=admin["headers"],
        )
        assert response.status_code == 202
        note = _receive_until(ws, "notification")["data"]
        assert note["title"] == "Maintenance" and note["sender"] == admin["username"]
