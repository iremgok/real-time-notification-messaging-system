from src.core.logger import redact


def test_health(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["database"] and body["redis"]


def test_register_duplicate_and_bad_login(client, make_user):
    user = make_user()
    duplicate = client.post(
        "/api/v1/auth/register",
        json={"username": user["username"], "email": "other@example.com", "password": "another-pass"},
    )
    assert duplicate.status_code == 409
    bad = client.post("/api/v1/auth/login", data={"username": user["username"], "password": "wrong-pass"})
    assert bad.status_code == 401


def test_me_requires_valid_token(client, make_user):
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    user = make_user()
    assert client.get("/api/v1/auth/me", headers=user["headers"]).json()["username"] == user["username"]


def test_direct_room_is_unique_and_history_is_member_only(client, make_user):
    alice, bob, eve = make_user("alice"), make_user("bob"), make_user("eve")
    first = client.post("/api/v1/rooms/direct", json={"target_username": bob["username"]}, headers=alice["headers"])
    second = client.post("/api/v1/rooms/direct", json={"target_username": alice["username"]}, headers=bob["headers"])
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    room_id = first.json()["id"]
    assert client.get(f"/api/v1/messages/{room_id}", headers=alice["headers"]).status_code == 200
    assert client.get(f"/api/v1/messages/{room_id}", headers=eve["headers"]).status_code == 403
    assert client.post(f"/api/v1/rooms/{room_id}/join", headers=eve["headers"]).status_code == 403


def test_broadcast_requires_admin(client, make_user):
    user = make_user()
    response = client.post("/api/v1/admin/broadcast", json={"title": "hi", "body": "there"}, headers=user["headers"])
    assert response.status_code == 403


def test_invalid_cursor_rejected(client, make_user):
    user = make_user()
    room = client.post("/api/v1/rooms", json={"name": "r"}, headers=user["headers"]).json()
    response = client.get(f"/api/v1/messages/{room['id']}?cursor=garbage", headers=user["headers"])
    assert response.status_code == 400


def test_redaction():
    text = redact("GET /ws?token=eyJabc.def.ghi password=hunter2 mail a@b.com Bearer xyz")
    for secret in ("hunter2", "eyJabc", "a@b.com", "xyz"):
        assert secret not in text
