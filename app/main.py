import logging
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.redis import init_redis, close_redis
from app.db.base import Base
from app.db.session import engine
from app.api.v1.router import api_router
from app.api.v1.endpoints import ws
from app.services.connection_manager import manager

# --- 1. Structured Logging and PII Masking Setup ---
class PIIMaskFilter(logging.Filter):
    """
    Custom logging filter that automatically masks PII in logs
    (e.g., passwords, authorization tokens, email addresses).
    """
    def __init__(self):
        super().__init__()
        # Regex patterns to find sensitive fields and mask their values
        self.patterns = [
            (re.compile(r'(?i)(password["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])'), r'\1[MASKED]\3'),
            (re.compile(r'(?i)(token["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])'), r'\1[MASKED]\3'),
            (re.compile(r'(?i)(bearer\s+)([A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.?[A-Za-z0-9\-_+/=]*)'), r'\1[MASKED]'),
            (re.compile(r'(?i)(email["\']?\s*[:=]\s*["\'])([^"\']+\@[^"\']+\.[^"\']+)(["\'])'), r'\1[MASKED]\3')
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, repl in self.patterns:
                record.msg = pattern.sub(repl, record.msg)
        return True


# Configure root logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Apply PII filter to standard logs
for handler in logging.root.handlers:
    handler.addFilter(PIIMaskFilter())

logger = logging.getLogger("app.main")


# --- 2. Application Lifespan Handler ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    logger.info("Initializing services on startup...")

    # 1. Initialize Redis connection
    await init_redis()

    # 2. Start WebSocket manager's Redis Pub/Sub listener
    manager.start_pubsub_listener()

    # 3. Automatic DB schema creation (fallback if migrations aren't run manually)
    async with engine.begin() as conn:
        logger.info("Verifying and generating database tables...")
        await conn.run_sync(Base.metadata.create_all)

    yield

    # Shutdown tasks
    logger.info("Shutting down services...")
    # 1. Stop WebSocket manager's Redis Pub/Sub listener
    manager.stop_pubsub_listener()
    # 2. Close Redis client
    await close_redis()
    # 3. Dispose DB engine connection pool
    await engine.dispose()
    logger.info("Shutdown completed.")


# --- 3. FastAPI Application Initialization ---
app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    version="1.0.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production requirements
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include v1 REST API Router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Include main WebSocket Endpoint
app.include_router(ws.router)

# Mount Static Files (for the client-side demo interface)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "docs_url": "/docs",
        "demo_url": "/static/index.html",
    }
