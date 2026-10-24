"""Programmatic Alembic runner used at startup and by ``python -m src.manage migrate``.

These functions are synchronous and call ``asyncio.run`` internally (via env.py), so
from async code run them in a worker thread: ``await asyncio.to_thread(upgrade_to_head)``.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import settings
from src.core.logger import get_logger
from src.models import entities  # noqa: F401  (registers every table on Base.metadata)
from src.models.database import Base

logger = get_logger("migrations")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

# Differences that mean the legacy schema cannot simply be adopted as "head".
_STRUCTURAL_DIFFS = {"add_table", "add_column", "remove_column"}


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["configure_logging"] = False
    if database_url:
        config.attributes["database_url"] = database_url
    return config


def upgrade_to_head(database_url: str | None = None) -> None:
    config = alembic_config(database_url)
    if asyncio.run(_is_unversioned_legacy_schema(database_url)):
        _adopt_legacy_schema(config, database_url)
    command.upgrade(config, "head")
    logger.info("Database schema is at head revision")


# --- Adoption of databases created before Alembic (Base.metadata.create_all) ---
async def _run_on_connection(database_url: str | None, fn):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database_url or settings.DATABASE_URL, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(fn)
    finally:
        await engine.dispose()


async def _is_unversioned_legacy_schema(database_url: str | None) -> bool:
    def check(connection: Connection) -> bool:
        tables = set(inspect(connection).get_table_names())
        return "users" in tables and "alembic_version" not in tables

    return await _run_on_connection(database_url, check)


def _adopt_legacy_schema(config: Config, database_url: str | None) -> None:
    """Stamp a pre-Alembic database as head *only* if its tables/columns already match."""

    def diff(connection: Connection) -> list[str]:
        context = MigrationContext.configure(connection)
        changes = []
        for change in compare_metadata(context, Base.metadata):
            op = change[0] if isinstance(change, tuple) else change[0][0]
            if op in _STRUCTURAL_DIFFS:
                changes.append(f"{op}: {change[1] if isinstance(change, tuple) else change[0][1]}")
        return changes

    structural = asyncio.run(_run_on_connection(database_url, diff))
    if structural:
        raise RuntimeError(
            "Existing database was created without Alembic and does not match the models "
            f"({len(structural)} structural differences, e.g. {structural[0]}). "
            "Back it up and migrate it manually, or recreate it."
        )
    command.stamp(config, "head")
    logger.warning("Adopted existing pre-Alembic schema: stamped as head without running migrations")
