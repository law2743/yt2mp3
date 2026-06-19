from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from app.api.auth import is_authenticated
from app.config import get_settings
from app.errors import AppError
from app.models import AnalyzeRequest, TransposeRequest
from app.services.files import safe_child, sanitize_filename
from app.services.job_manager import JobManager
from app.services.key_names import display_key
from app.services.youtube import canonicalize_youtube_url

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _context(request: Request) -> tuple[JobManager, str]:
    settings = get_settings()
    if not is_authenticated(request, settings):
        raise AppError(401, "AUTH_REQUIRED", "請先登入。")
    owner_id = request.session.get("owner_id")
    if not owner_id:
        owner_id = str(uuid.uuid4())
        request.session["owner_id"] = owner_id
    return request.app.state.job_manager, owner_id


@router.post("/analyze", status_code=202)
async def analyze(payload: AnalyzeRequest, request: Request):
    manager, owner_id = _context(request)
    job = await manager.create(owner_id, canonicalize_youtube_url(payload.url))
    return {"job_id": job.job_id, "status": job.status, "status_url": f"/api/jobs/{job.job_id}"}


@router.get("/{job_id}")
async def status(job_id: str, request: Request):
    manager, owner_id = _context(request)
    return manager.public(manager.get(job_id, owner_id))


@router.post("/{job_id}/transpose", status_code=202)
async def transpose(job_id: str, payload: TransposeRequest, request: Request):
    manager, owner_id = _context(request)
    job = manager.get(job_id, owner_id)
    cached = await manager.request_transpose(job, payload.semitones)
    assert job.analysis
    return {
        "job_id": job.job_id,
        "status": "completed" if cached else job.status,
        "semitones": payload.semitones,
        "target_key": display_key(job.analysis.root_index + payload.semitones, job.analysis.mode),
        "cached": cached is not None,
    }


@router.get("/{job_id}/download/{semitones}")
async def download(job_id: str, semitones: int, request: Request):
    manager, owner_id = _context(request)
    job = manager.get(job_id, owner_id)
    path = job.outputs.get(semitones)
    if not path or not path.exists():
        raise AppError(404, "OUTPUT_NOT_FOUND", "指定的轉調檔案不存在或已失效。")
    assert job.source_info and job.analysis
    target = display_key(job.analysis.root_index + semitones, job.analysis.mode)
    shift = "original" if semitones == 0 else f"{'up' if semitones > 0 else 'down'}-{abs(semitones)}"
    filename = sanitize_filename(f"{job.source_info.title}_{shift}_{target}") + ".mp3"
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=filename,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{job_id}/thumbnail")
async def thumbnail(job_id: str, request: Request):
    manager, owner_id = _context(request)
    job = manager.get(job_id, owner_id)
    path = safe_child(job.root, "thumbnail.jpg")
    if not path.exists():
        raise AppError(404, "THUMBNAIL_NOT_FOUND", "找不到影片縮圖。")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str, request: Request):
    manager, owner_id = _context(request)
    await manager.delete(manager.get(job_id, owner_id))
    return Response(status_code=204)

