from fastapi import APIRouter
from app.api.v1.endpoints import auth, chat

api_router = APIRouter()

# Mount auth endpoints under /auth prefix
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])

# Mount chat endpoints (rooms, messages)
api_router.include_router(chat.router, tags=["Chat Operations"])
