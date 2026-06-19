from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.errors import AppError
from app.services.files import safe_child
from app.services.process import ProcessFailed, ProcessTimedOut, run_process


async def prepare_analysis_audio(source: Path, job_root: Path, settings: Settings) -> Path:
    output = safe_child(job_root, "analysis.wav")
    try:
        await run_process(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-vn", "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(output),
            ],
            timeout=settings.analysis_timeout_seconds,
        )
    except ProcessTimedOut as exc:
        raise AppError(504, "PROCESS_TIMEOUT", "音訊準備超過時間限制。", True) from exc
    except ProcessFailed as exc:
        raise AppError(500, "ANALYSIS_FAILED", "無法準備音訊以分析調性。") from exc
    await probe_audio(output, settings)
    return output


async def probe_audio(path: Path, settings: Settings) -> dict:
    try:
        result = await run_process(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration,size:stream=codec_type,codec_name,sample_rate,channels",
                "-of", "json", str(path),
            ],
            timeout=min(settings.metadata_timeout_seconds, 30),
        )
        data = json.loads(result.stdout)
    except (ProcessFailed, ProcessTimedOut, json.JSONDecodeError) as exc:
        raise AppError(500, "AUDIO_FORMAT_UNAVAILABLE", "音訊檔案無法解碼。") from exc
    if not any(stream.get("codec_type") == "audio" for stream in data.get("streams", [])):
        raise AppError(500, "AUDIO_FORMAT_UNAVAILABLE", "找不到可處理的音訊格式。")
    return data


async def transpose_audio(
    source: Path,
    job_root: Path,
    semitones: int,
    title: str,
    uploader: str | None,
    target_key: str,
    settings: Settings,
) -> Path:
    output_dir = safe_child(job_root, "output")
    output_dir.mkdir(exist_ok=True)
    output = safe_child(output_dir, f"shift_{semitones}.mp3")
    pcm = safe_child(job_root, "transpose_source.wav")
    shifted = safe_child(job_root, f"shifted_{semitones}.wav")
    thumbnail = safe_child(job_root, "thumbnail.jpg")

    try:
        await run_process(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-vn", "-ar", "44100", "-c:a", "pcm_s24le", str(pcm)],
            timeout=settings.transpose_timeout_seconds,
        )
        input_audio = pcm
        if semitones:
            await run_process(
                ["rubberband", "-3", "-p", str(semitones), str(pcm), str(shifted)],
                timeout=settings.transpose_timeout_seconds,
            )
            input_audio = shifted

        shift_text = "原調" if semitones == 0 else f"{'升' if semitones > 0 else '降'}{abs(semitones)}半音"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_audio)]
        if thumbnail.exists():
            command.extend(["-i", str(thumbnail), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg",
                            "-disposition:v", "attached_pic"])
        command.extend([
            "-c:a", "libmp3lame", "-q:a", "0", "-ar", "44100",
            "-metadata", f"title={title} [{shift_text}・{target_key}]",
            "-metadata", f"artist={uploader or ''}",
            "-metadata", "comment=Transposed by yt2mp3",
            str(output),
        ])
        await run_process(command, timeout=settings.transpose_timeout_seconds)
        await probe_audio(output, settings)
    except ProcessTimedOut as exc:
        raise AppError(504, "PROCESS_TIMEOUT", "轉調時間超過限制。", True) from exc
    except ProcessFailed as exc:
        raise AppError(500, "TRANSPOSE_FAILED", "音訊轉調失敗，請重新嘗試。", True) from exc
    finally:
        pcm.unlink(missing_ok=True)
        shifted.unlink(missing_ok=True)
    return output
