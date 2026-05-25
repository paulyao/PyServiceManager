"""PyService Manager - FastAPI application entry point."""
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

from app.config import API_PREFIX, HOST, PORT, DATA_DIR, SERVICES_DIR, MODULES_DIR
from app.database import init_db
from app.core.service_manager import ServiceManager
from app.core.config_watcher import ConfigWatcher


# Global instances
service_manager = ServiceManager()
config_watcher = ConfigWatcher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    MODULES_DIR.mkdir(parents=True, exist_ok=True)

    await init_db()

    # Ensure built-in modules are registered
    from app.core.module_manager import ModuleManager
    from app.database import async_session
    async with async_session() as session:
        module_manager = ModuleManager()
        created = await module_manager.ensure_builtin_modules(session)
        if created:
            logger.info(f"Registered built-in modules: {created}")

    await config_watcher.start()

    # Repair module paths (fix absolute/stale paths from dev environment)
    from app.core.module_manager import ModuleManager
    from app.database import async_session
    async with async_session() as session:
        module_manager = ModuleManager()
        repaired = await module_manager.repair_paths(session)
        if repaired:
            logger.info(f"Repaired {repaired} module path entries")

    # Sync all service statuses
    from app.database import async_session
    async with async_session() as session:
        await service_manager.sync_all_status(session)

    yield

    # Shutdown
    from app.core.log_streamer import log_streamer
    log_streamer.shutdown()  # 通知所有 WebSocket 循环退出
    await config_watcher.stop()


app = FastAPI(
    title="PyService Manager",
    description="Python Service Management Platform with Module System",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware - allow cross-origin requests for web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler
from fastapi import Request
from fastapi.responses import JSONResponse


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Let FastAPI's built-in handlers process these
    if isinstance(exc, (HTTPException, RequestValidationError)):
        raise exc

    # Log unexpected exceptions with traceback
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")

    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}},
    )

# Import and register routers
from app.api.services import router as services_router
from app.api.config import router as config_router
from app.api.logs import router as logs_router
from app.api.modules import router as modules_router
from app.api.backup import router as backup_router

app.include_router(services_router, prefix=API_PREFIX)
app.include_router(config_router, prefix=API_PREFIX)
app.include_router(logs_router, prefix=API_PREFIX)
app.include_router(modules_router, prefix=API_PREFIX)
app.include_router(backup_router, prefix=API_PREFIX)


# Serve static files
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    """Serve the main page."""
    from fastapi.responses import FileResponse
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "PyService Manager is running. Static files not found."}


@app.get(f"{API_PREFIX}/health")
async def health():
    return {"status": "ok", "backend": service_manager.backend_name}


def cli():
    """CLI entry point."""
    reload = os.getenv("PYSERVICE_RELOAD", "true").lower() in ("true", "1", "yes")
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=reload, timeout_graceful_shutdown=3)


if __name__ == "__main__":
    cli()
