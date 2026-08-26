import logging
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    decode_access_token,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import UserCreate, UserResponse, Token

logger = logging.getLogger("app.auth")
router = APIRouter()

# Setup OAuth2 token scheme
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login"
)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Dependency to validate JWT token and return the current user.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id_str = decode_access_token(token)
    if user_id_str is None:
        raise credentials_exception

    try:
        stmt = select(User).where(User.id == user_id_str)
        result = await db.execute(stmt)
        user = result.scalars().first()
        if user is None:
            raise credentials_exception
        return user
    except Exception as e:
        logger.error(f"Error fetching current user: {e}")
        raise credentials_exception


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_in: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]
):
    """
    Create a new user. Checks for existing username or email.
    """
    try:
        # Check if username or email already exists
        stmt = select(User).where(
            (User.username == user_in.username) | (User.email == user_in.email)
        )
        result = await db.execute(stmt)
        existing_user = result.scalars().first()

        if existing_user:
            if existing_user.username == user_in.username:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already registered",
                )
            if existing_user.email == user_in.email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered",
                )

        new_user = User(
            username=user_in.username,
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
        )

        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # Log structure with masked details (avoiding logging raw inputs/PII)
        logger.info(f"User successfully registered: ID={new_user.id}")
        return new_user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to register user: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed",
        )


@router.post("/login", response_model=Token)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    OAuth2 compatible token login, returning an access token.
    """
    try:
        stmt = select(User).where(User.username == form_data.username)
        result = await db.execute(stmt)
        user = result.scalars().first()

        if not user or not verify_password(form_data.password, user.hashed_password):
            logger.warning(
                f"Failed login attempt for username: {form_data.username}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        access_token = create_access_token(subject=str(user.id))
        logger.info(f"User logged in successfully: ID={user.id}")
        return Token(access_token=access_token, token_type="bearer")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed",
        )
