"""FastAPI application entry point and lifespan management."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api.errors import register_exception_handlers
from src.api.routes import api_router
from src.core.config import settings
from src.core.logger import get_logger, setup_logging
from src.models.database import dispose_engine
from src.models.migrations import upgrade_to_head
from src.services.redis_service import redis_service
from src.websockets.endpoints import router as ws_router
from src.websockets.manager import manager

setup_logging()
logger = get_logger("main")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s (env=%s)", settings.PROJECT_NAME, settings.ENVIRONMENT)
    if settings.DB_AUTO_MIGRATE:
        # Alembic's env.py runs its own event loop, so migrate in a worker thread.
        await asyncio.to_thread(upgrade_to_head)
    await redis_service.connect()
    redis_service.start_listener(manager.dispatch)
    try:
        yield
    finally:
        logger.info("Shutting down")
        await redis_service.stop_listener()
        await manager.close_all()
        await redis_service.close()
        await dispose_engine()


def create_app() -> FastAPI:
    is_production = settings.ENVIRONMENT == "production"
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    app.include_router(ws_router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> RedirectResponse:
            return RedirectResponse("/static/index.html")

    return app


app = create_app()
