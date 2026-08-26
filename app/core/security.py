from datetime import datetime, timedelta, timezone
from typing import Any, Union
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(
    subject: Union[str, Any], expires_delta: Union[timedelta, None] = None
) -> str:
    """
    Generate a JWT token for the authenticated subject.
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against the stored bcrypt hash.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Generate bcrypt hash for a plain password.
    """
    return pwd_context.hash(password)


def decode_access_token(token: str) -> Union[str, None]:
    """
    Decode a JWT token and extract the subject (user_id).
    Returns None if token is invalid or expired.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        token_data = payload.get("sub")
        if token_data is None:
            return None
        return token_data
    except JWTError:
        return None
