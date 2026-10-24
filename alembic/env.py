"""Alembic environment: async engine, models' metadata and settings-driven DATABASE_URL.

The URL comes from ``src.core.config`` (env vars / .env) and is never written to
alembic.ini or printed. Logs go through the project's redacting logger.
"""

import asyncio
import logging

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import settings
from src.core.logger import setup_logging
from src.models import entities  # noqa: F401  (registers every table on Base.metadata)
from src.models.database import Base

config = context.config

# The app configures logging itself before running migrations programmatically.
if config.attributes.get("configure_logging", True):
    setup_logging()
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

target_metadata = Base.metadata

# Arbitrary constant; serialises concurrent `upgrade` runs (e.g. several replicas starting).
_MIGRATION_LOCK_ID = 7_242_311


def _database_url() -> str:
    return config.attributes.get("database_url") or settings.DATABASE_URL


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,  # SQLite cannot ALTER most things; batch mode recreates tables
        **kwargs,
    )


def run_migrations_offline() -> None:
    """``alembic upgrade head --sql``: emit SQL to stdout instead of executing it."""
    _configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            # Held until the migration transaction commits; other runners wait, then
            # find the schema already at head and do nothing.
            connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _MIGRATION_LOCK_ID})
        context.run_migrations()


async def run_migrations_online() -> None:
    # Dedicated engine: NullPool (one-off), parameters hidden from errors, and on SQLite
    # foreign keys stay OFF so batch table rebuilds cannot cascade-delete rows.
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
