"""Test setup: temporary SQLite database and in-memory fakeredis (no external services)."""

import os
import tempfile
import uuid

_DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(
    ENVIRONMENT="test",
    DATABASE_URL=f"sqlite+aiosqlite:///{_DB_PATH}",
    WS_RATE_LIMIT_COUNT="5",
    AUTH_RATE_LIMIT_COUNT="1000",
    WS_ALLOWED_ORIGINS="",
)

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.services.redis_service import redis_service  # noqa: E402

_fake_server = fakeredis.FakeServer()
redis_service.client_factory = lambda: fakeredis.FakeAsyncRedis(server=_fake_server, decode_responses=True)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user(client):
    def _make(prefix: str = "user") -> dict:
        username = f"{prefix}_{uuid.uuid4().hex[:8]}"
        password = "s3cret-password"
        response = client.post(
            "/api/v1/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": password},
        )
        assert response.status_code == 201, response.text
        login = client.post("/api/v1/auth/login", data={"username": username, "password": password})
        token = login.json()["access_token"]
        return {
            "id": response.json()["id"],
            "username": username,
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return _make
