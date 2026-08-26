from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

# Create asynchronous engine for PostgreSQL
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # Set to True for debugging SQL queries
    future=True,
    pool_pre_ping=True,
)

# Async session maker
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency generator for async SQLAlchemy DB sessions.
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
