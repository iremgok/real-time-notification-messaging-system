"""SQLAlchemy declarative base, async engine and session management."""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.core.config import settings


# Deterministic constraint names: migrations produce identical schemas on every database
# and SQLite batch migrations can find constraints by name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _engine_options() -> dict[str, Any]:
    # hide_parameters keeps bound values (e-mails, password hashes) out of error messages/logs.
    options: dict[str, Any] = {"pool_pre_ping": True, "hide_parameters": True}
    if not settings.is_sqlite:
        options.update(pool_size=settings.DB_POOL_SIZE, max_overflow=settings.DB_POOL_SIZE * 2)
    return options


engine = create_async_engine(settings.DATABASE_URL, **_engine_options())

if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed."""
    async with SessionFactory() as session:
        yield session


async def dispose_engine() -> None:
    await engine.dispose()
