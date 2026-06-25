from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse

from app.api.auth import authenticated_owner
from app.config import Settings, get_settings
from app.errors import AppError
from app.models import AnalyzeRequest, MelodyRequest, MelodyStatus, StemRequest, TransposeRequest
from app.models.stem import StemTaskStatus
from app.services.audio import encode_mp3
from app.services.files import safe_child, sanitize_filename
from app.services.job_manager import JobManager
from app.services.key_names import display_key
from app.services.youtube import canonicalize_youtube_url

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _context(request: Request, owner_id: str) -> tuple[JobManager, str]:
    return request.app.state.job_manager, owner_id


@router.post("/analyze", status_code=202)
async def analyze(
    payload: AnalyzeRequest,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = await manager.create(owner_id, canonicalize_youtube_url(payload.url))
    return {"job_id": job.job_id, "status": job.status, "status_url": f"/api/jobs/{job.job_id}"}


@router.get("/{job_id}")
async def status(job_id: str, request: Request, owner_id: str = Depends(authenticated_owner)):
    manager, owner_id = _context(request, owner_id)
    return manager.public(manager.get(job_id, owner_id))


@router.post("/{job_id}/melody", status_code=202)
async def create_melody(
    job_id: str,
    payload: MelodyRequest,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = manager.get(job_id, owner_id)
    cached = await manager.request_melody(job, payload.force, payload.meter_hint, payload.source)
    response = manager.melody_public(job)
    response["cached"] = cached
    response["status_url"] = f"/api/jobs/{job.job_id}/melody"
    return response


@router.get("/{job_id}/melody")
async def melody_status(
    job_id: str,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    return manager.melody_public(manager.get(job_id, owner_id))


@router.post("/{job_id}/stems", status_code=202)
async def create_stems(
    job_id: str,
    payload: StemRequest,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = manager.get(job_id, owner_id)
    cached = await manager.request_stems(job, payload.force)
    response = manager.stems_public(job)
    response["cached"] = cached
    response["status_url"] = f"/api/jobs/{job.job_id}/stems"
    return response


@router.get("/{job_id}/stems")
async def stems_status(
    job_id: str,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    return manager.stems_public(manager.get(job_id, owner_id))


def _completed_stems(manager: JobManager, job_id: str, owner_id: str):
    job = manager.get(job_id, owner_id)
    if job.stems.status != StemTaskStatus.COMPLETED:
        raise AppError(404, "STEM_OUTPUT_NOT_FOUND", "人聲／伴奏檔案不存在或尚未完成。")
    return job


@router.get("/{job_id}/stems/vocals")
async def download_vocals(
    job_id: str,
    request: Request,
    bitrate_kbps: int = Query(default=192),
    owner_id: str = Depends(authenticated_owner),
    settings: Settings = Depends(get_settings),
):
    manager, owner_id = _context(request, owner_id)
    job = _completed_stems(manager, job_id, owner_id)
    if not job.artifacts.vocals_wav.exists():
        raise AppError(404, "STEM_OUTPUT_NOT_FOUND", "人聲 stem 不存在或已失效。")
    if bitrate_kbps not in {128, 192, 256}:
        raise AppError(422, "INVALID_BITRATE", "請選擇畫面提供的位元率。")
    output = job.artifacts.stem_mp3("vocals", bitrate_kbps)
    if not output.exists() or output.stat().st_mtime < job.artifacts.vocals_wav.stat().st_mtime:
        title = f"{job.source_info.title} 人聲" if job.source_info else "yt2mp3 vocals"
        artist = job.source_info.uploader if job.source_info else None
        await encode_mp3(job.artifacts.vocals_wav, output, settings, bitrate_kbps=bitrate_kbps, title=title, artist=artist)
    return FileResponse(
        output,
        media_type="audio/mpeg",
        filename=f"vocals_{job.job_id}_{bitrate_kbps}k.mp3",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{job_id}/stems/accompaniment")
async def download_accompaniment(
    job_id: str,
    request: Request,
    bitrate_kbps: int = Query(default=192),
    owner_id: str = Depends(authenticated_owner),
    settings: Settings = Depends(get_settings),
):
    manager, owner_id = _context(request, owner_id)
    job = _completed_stems(manager, job_id, owner_id)
    if not job.artifacts.accompaniment_wav.exists():
        raise AppError(404, "STEM_OUTPUT_NOT_FOUND", "伴奏 stem 不存在或已失效。")
    if bitrate_kbps not in {128, 192, 256}:
        raise AppError(422, "INVALID_BITRATE", "請選擇畫面提供的位元率。")
    output = job.artifacts.stem_mp3("accompaniment", bitrate_kbps)
    if not output.exists() or output.stat().st_mtime < job.artifacts.accompaniment_wav.stat().st_mtime:
        title = f"{job.source_info.title} 伴奏" if job.source_info else "yt2mp3 accompaniment"
        artist = job.source_info.uploader if job.source_info else None
        await encode_mp3(job.artifacts.accompaniment_wav, output, settings, bitrate_kbps=bitrate_kbps, title=title, artist=artist)
    return FileResponse(
        output,
        media_type="audio/mpeg",
        filename=f"accompaniment_{job.job_id}_{bitrate_kbps}k.mp3",
        headers={"Cache-Control": "private, no-store"},
    )


def _completed_melody(manager: JobManager, job_id: str, owner_id: str):
    job = manager.get(job_id, owner_id)
    if job.melody.status != MelodyStatus.COMPLETED:
        raise AppError(404, "MELODY_OUTPUT_NOT_FOUND", "主旋律分析檔案不存在或尚未完成。")
    return job


@router.get("/{job_id}/melody/download/json")
async def download_melody_json(
    job_id: str,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = _completed_melody(manager, job_id, owner_id)
    path = job.artifacts.melody_json
    if not path.exists():
        raise AppError(404, "MELODY_OUTPUT_NOT_FOUND", "主旋律分析檔案不存在或尚未完成。")
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"melody_{job.job_id}.json",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{job_id}/melody/download/midi")
async def download_melody_midi(
    job_id: str,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = _completed_melody(manager, job_id, owner_id)
    path = job.artifacts.melody_midi
    if not path.exists():
        raise AppError(404, "MELODY_OUTPUT_NOT_FOUND", "主旋律分析檔案不存在或尚未完成。")
    return FileResponse(
        path,
        media_type="audio/midi",
        filename=f"melody_{job.job_id}.mid",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{job_id}/transpose", status_code=202)
async def transpose(
    job_id: str,
    payload: TransposeRequest,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = manager.get(job_id, owner_id)
    cached = await manager.request_transpose(job, payload.semitones, payload.bitrate_kbps)
    assert job.analysis
    return {
        "job_id": job.job_id,
        "status": "completed" if cached else job.status,
        "semitones": payload.semitones,
        "target_key": display_key(job.analysis.root_index + payload.semitones, job.analysis.mode),
        "bitrate_kbps": payload.bitrate_kbps,
        "cached": cached is not None,
    }


@router.get("/{job_id}/download/{semitones}")
async def download(
    job_id: str,
    semitones: int,
    request: Request,
    bitrate_kbps: int = Query(default=192),
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = manager.get(job_id, owner_id)
    if bitrate_kbps not in {128, 192, 256}:
        raise AppError(422, "INVALID_BITRATE", "請選擇畫面提供的位元率。")
    path = job.outputs.get((semitones, bitrate_kbps))
    if not path or not path.exists():
        raise AppError(404, "OUTPUT_NOT_FOUND", "指定的轉調檔案不存在或已失效。")
    assert job.source_info and job.analysis
    target = display_key(job.analysis.root_index + semitones, job.analysis.mode)
    shift = (
        "original" if semitones == 0 else f"{'up' if semitones > 0 else 'down'}-{abs(semitones)}"
    )
    filename = (
        sanitize_filename(f"{job.source_info.title}_{shift}_{target}_{bitrate_kbps}kbps") + ".mp3"
    )
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=filename,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{job_id}/thumbnail")
async def thumbnail(
    job_id: str,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    job = manager.get(job_id, owner_id)
    path = safe_child(job.root, "thumbnail.jpg")
    if not path.exists():
        raise AppError(404, "THUMBNAIL_NOT_FOUND", "找不到影片縮圖。")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"}
    )


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    request: Request,
    owner_id: str = Depends(authenticated_owner),
):
    manager, owner_id = _context(request, owner_id)
    await manager.delete(manager.get(job_id, owner_id))
    return Response(status_code=204)
