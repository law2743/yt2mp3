from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api.auth import is_authenticated, router as auth_router
from app.api.jobs import router as jobs_router
from app.config import get_settings
from app.errors import AppError, app_error_handler
from app.services.job_manager import JobManager

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    settings = get_settings()
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
app = FastAPI(title="yt2mp3", version=__version__, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.app_env == "production",
    max_age=60 * 60 * 12,
)
app.add_exception_handler(AppError, app_error_handler)
app.include_router(auth_router)
app.include_router(jobs_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


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
                content={"error": {"code": "REQUEST_TOO_LARGE", "message": "請求內容過大。", "retryable": False}},
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


@app.get("/")
async def index(request: Request):
    if not is_authenticated(request, settings):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/login")
async def login_page(request: Request):
    if is_authenticated(request, settings):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": request.query_params.get("error")})


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "dependencies": {
            "yt_dlp": shutil.which("yt-dlp") is not None,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ffprobe": shutil.which("ffprobe") is not None,
            "rubberband": shutil.which("rubberband") is not None,
        },
    }
