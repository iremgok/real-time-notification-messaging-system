"""Alembic migrations: upgrade/downgrade round trip, model drift and legacy adoption."""

import os
import tempfile

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from src.models.database import Base
from src.models.migrations import alembic_config, upgrade_to_head

APP_TABLES = {"users", "rooms", "memberships", "messages"}


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "migrations.db")
    yield path


def _async_url(path: str) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _sync_engine(path: str):
    return create_engine(f"sqlite:///{path}")


def _tables(path: str) -> set[str]:
    engine = _sync_engine(path)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _version(path: str) -> str | None:
    engine = _sync_engine(path)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def test_upgrade_creates_schema_matching_models(db_path):
    """Fails when a model changes without a corresponding migration."""
    upgrade_to_head(_async_url(db_path))

    engine = _sync_engine(db_path)
    try:
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"Models and migrations are out of sync, run `alembic revision --autogenerate`: {diff}"
    assert APP_TABLES <= _tables(db_path)


def test_downgrade_to_base_and_upgrade_again(db_path):
    url = _async_url(db_path)
    upgrade_to_head(url)
    command.downgrade(alembic_config(url), "base")
    assert _tables(db_path) & APP_TABLES == set()

    upgrade_to_head(url)
    assert APP_TABLES <= _tables(db_path)


def test_upgrade_is_idempotent(db_path):
    url = _async_url(db_path)
    upgrade_to_head(url)
    head = _version(db_path)
    upgrade_to_head(url)
    assert _version(db_path) == head


def test_pre_alembic_database_matching_models_is_stamped(db_path):
    engine = _sync_engine(db_path)
    Base.metadata.create_all(engine)  # how databases were created before Alembic
    engine.dispose()

    upgrade_to_head(_async_url(db_path))
    assert _version(db_path) is not None


def test_pre_alembic_database_with_different_schema_is_refused(db_path):
    engine = _sync_engine(db_path)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY, username VARCHAR(50))"))
    engine.dispose()

    with pytest.raises(RuntimeError, match="does not match the models"):
        upgrade_to_head(_async_url(db_path))
    assert "alembic_version" not in _tables(db_path)
