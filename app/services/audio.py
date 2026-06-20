from __future__ import annotations

import json
import re
from collections.abc import Callable
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
    bitrate_kbps: int = 192,
    progress_callback: Callable[[int], None] | None = None,
) -> Path:
    output_dir = safe_child(job_root, "output")
    output_dir.mkdir(exist_ok=True)
    output = safe_child(output_dir, f"shift_{semitones}_{bitrate_kbps}k.mp3")
    pcm = safe_child(job_root, "transpose_source.wav")
    shifted = safe_child(job_root, f"shifted_{semitones}.wav")
    thumbnail = safe_child(job_root, "thumbnail.jpg")

    def report(percent: int) -> None:
        if progress_callback:
            progress_callback(min(100, max(0, percent)))

    try:
        report(2)
        await run_process(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-vn", "-ar", "44100", "-c:a", "pcm_s24le", str(pcm)],
            timeout=settings.transpose_timeout_seconds,
        )
        report(15 if semitones else 55)
        input_audio = pcm
        if semitones:
            rubberband_pass = 1

            def rubberband_progress(line: str) -> None:
                nonlocal rubberband_pass
                if "Pass 1:" in line:
                    rubberband_pass = 1
                elif "Pass 2:" in line:
                    rubberband_pass = 2
                match = re.fullmatch(r"\s*(\d{1,3})%\s*", line)
                if not match:
                    return
                percent = min(100, int(match.group(1)))
                if rubberband_pass == 1:
                    report(15 + round(percent * 0.35))
                else:
                    report(50 + round(percent * 0.40))

            await run_process(
                ["rubberband", "-3", "-p", str(semitones), str(pcm), str(shifted)],
                timeout=settings.transpose_timeout_seconds,
                stderr_line_callback=rubberband_progress,
            )
            input_audio = shifted
            report(90)

        shift_text = "原調" if semitones == 0 else f"{'升' if semitones > 0 else '降'}{abs(semitones)}半音"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_audio)]
        if thumbnail.exists():
            command.extend(["-i", str(thumbnail), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg",
                            "-disposition:v", "attached_pic"])
        command.extend([
            "-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k", "-ar", "44100",
            "-metadata", f"title={title} [{shift_text}・{target_key}]",
            "-metadata", f"artist={uploader or ''}",
            "-metadata", "comment=Transposed by yt2mp3",
            str(output),
        ])
        await run_process(command, timeout=settings.transpose_timeout_seconds)
        report(98)
        await probe_audio(output, settings)
        report(100)
    except ProcessTimedOut as exc:
        raise AppError(504, "PROCESS_TIMEOUT", "轉調時間超過限制。", True) from exc
    except ProcessFailed as exc:
        raise AppError(500, "TRANSPOSE_FAILED", "音訊轉調失敗，請重新嘗試。", True) from exc
    finally:
        pcm.unlink(missing_ok=True)
        shifted.unlink(missing_ok=True)
    return output
