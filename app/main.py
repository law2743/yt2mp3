from __future__ import annotations

import logging
import shutil
from importlib.util import find_spec
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.auth import router as auth_router
from app.api.jobs import router as jobs_router
from app.config import get_settings
from app.errors import AppError, app_error_handler
from app.services.job_manager import JobManager


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # Honor FastAPI's settings override in tests while keeping the cached
    # environment-backed settings in normal application startup.
    settings_provider = app_instance.dependency_overrides.get(get_settings, get_settings)
    settings = settings_provider()
    settings.work_root.mkdir(parents=True, exist_ok=True)
    manager = JobManager(settings)
    app_instance.state.job_manager = manager
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s level=%(levelname)s logger=%(name)s message=%(message)s",
)
app = FastAPI(
    title="yt2mp3",
    version=__version__,
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_private_network=True,
    expose_headers=["Content-Disposition"],
    max_age=600,
)
app.add_exception_handler(AppError, app_error_handler)
app.include_router(auth_router)
app.include_router(jobs_router)


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            too_large = int(content_length) > 16 * 1024
        except ValueError:
            too_large = True
        if too_large:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "REQUEST_TOO_LARGE",
                        "message": "請求內容過大。",
                        "retryable": False,
                    }
                },
            )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, _exc: RequestValidationError):
    is_shift = request.url.path.endswith("/transpose")
    return JSONResponse(
        status_code=422 if is_shift else 400,
        content={
            "error": {
                "code": "INVALID_SHIFT" if is_shift else "INVALID_REQUEST",
                "message": "請選擇畫面提供的升降半音數。" if is_shift else "請求格式不正確。",
                "retryable": False,
            }
        },
    )


@app.get("/", include_in_schema=False)
async def api_root():
    return {"service": "yt2mp3-api", "version": __version__}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "dependencies": {
            "yt_dlp": find_spec("yt_dlp") is not None,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ffprobe": shutil.which("ffprobe") is not None,
            "rubberband": shutil.which("rubberband") is not None,
        },
    }
