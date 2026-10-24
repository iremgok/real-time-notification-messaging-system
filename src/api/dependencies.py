"""Reusable FastAPI dependencies: DB session, current user, admin guard, rate limits."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.models.database import get_session
from src.models.entities import User
from src.services import auth_service
from src.services.rate_limiter import auth_limiter

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep) -> User:
    resolved = await auth_service.resolve_token(session, token)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return resolved[0]


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def limit_auth_attempts(request: Request) -> None:
    """Per-IP throttle for login/register (brute force & signup spam protection).

    Behind the bundled nginx, uvicorn runs with ``--proxy-headers`` so
    ``request.client.host`` is the real client address.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not await auth_limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts, try again later",
            headers={"Retry-After": str(settings.AUTH_RATE_LIMIT_WINDOW_SECONDS)},
        )
