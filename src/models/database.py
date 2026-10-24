"""SQLAlchemy declarative base, async engine and session management."""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.core.config import settings


class Base(DeclarativeBase):
    pass


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


async def create_tables() -> None:
    """Create missing tables. Use a migration tool for schema *changes* in production."""
    from src.models import entities  # noqa: F401  (registers the mappers)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await engine.dispose()
