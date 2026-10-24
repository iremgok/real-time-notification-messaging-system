"""Password hashing, JWT issuing/verification and user lookups."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import bcrypt
import jwt
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.logger import get_logger
from src.models.entities import User
from src.models.schemas import UserCreate
from src.services.exceptions import ConflictError

logger = get_logger("auth")

_TOKEN_TYPE = "access"


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    expires_at: datetime


# --- Passwords (bcrypt is CPU bound -> run off the event loop) ---
def _hash_password_sync(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password_sync(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:  # malformed hash or >72 byte password
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hash_password_sync("timing-equalizer-password")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash_password_sync, password)


async def verify_password(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(_verify_password_sync, password, hashed)


# --- JWT ---
def create_access_token(user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": _TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> TokenClaims | None:
    """Return the verified claims, or ``None`` for any invalid/expired/tampered token."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
        if payload.get("type") != _TOKEN_TYPE:
            return None
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


# --- Users ---
async def register_user(session: AsyncSession, data: UserCreate) -> User:
    email = data.email.lower()
    existing = await session.scalar(
        select(User.id).where(or_(User.username == data.username, User.email == email))
    )
    if existing is not None:
        raise ConflictError("Username or email already registered")

    user = User(username=data.username, email=email, hashed_password=await hash_password(data.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:  # concurrent registration with the same identity
        await session.rollback()
        raise ConflictError("Username or email already registered") from exc

    logger.info("User registered user_id=%s", user.id)
    return user


async def authenticate_user(session: AsyncSession, username: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.username == username))
    if user is None:
        # Spend the same time as a real check so usernames cannot be enumerated via timing.
        await verify_password(password, _dummy_hash())
        return None
    if not user.is_active or not await verify_password(password, user.hashed_password):
        return None
    return user


async def get_active_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    user = await session.get(User, user_id)
    return user if user is not None and user.is_active else None


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(User.username == username, User.is_active.is_(True)))


async def resolve_token(session: AsyncSession, token: str) -> tuple[User, TokenClaims] | None:
    claims = decode_access_token(token)
    if claims is None:
        return None
    user = await get_active_user(session, claims.user_id)
    return (user, claims) if user is not None else None
