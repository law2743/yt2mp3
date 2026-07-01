from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.errors import AppError
from app.models.melody import MelodyAnalysisResult, MelodySource, MelodyStatus, MeterHint
from app.services.audio import probe_audio
from app.services.gpu_subprocess_env import build_gpu_subprocess_env
from app.services.melody import analyze_fusion_melody
from app.services.melody_fusion import (
    FusionConfig,
    PostprocessArtifacts,
    fuse_pitch_csvs,
    postprocess_melody_fusion_artifacts,
)
from app.services.melody_fusion.io import (
    pitch_csv_rows_from_rmvpe_json,
    save_diagnostics,
    summarize_pitch_csv,
    write_pitch_csv,
)
from app.services.pipelines.melody import resolve_melody_source, sync_best_melody_alias
from app.services.pipelines.stems import read_stem_metadata
from app.services.process import ProcessFailed, ProcessTimedOut, run_process

if TYPE_CHECKING:
    from app.services.job_manager import Job


BACKENDS = ("rmvpe", "torchcrepe", "fcpe", "pesto")
MIN_SUCCESSFUL_BACKENDS = 2


class MelodyFusionPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(
        self, job: Job, meter_hint: MeterHint, requested_source: MelodySource = "auto"
    ) -> MelodyAnalysisResult:
        assert job.analysis
        source_used, source = resolve_melody_source(job, requested_source)
        if not source.exists():
            raise AppError(422, "MELODY_SOURCE_NOT_READY", "請先完成歌曲分析後再產生主旋律。")

        job.melody.status = MelodyStatus.PREPARING
        job.melody.stage = "preparing"
        job.melody.progress = 5
        job.artifacts.melody_dir.mkdir(parents=True, exist_ok=True)
        job.artifacts.melody_fusion_inputs_dir.mkdir(parents=True, exist_ok=True)

        temporary_json = job.artifacts.melody_dir / f".{source_used}_adaptive_fusion.json.tmp"
        temporary_midi = job.artifacts.melody_dir / f".{source_used}_adaptive_fusion.mid.tmp"
        for path in (temporary_json, temporary_midi):
            path.unlink(missing_ok=True)

        diagnostics = self._base_diagnostics()
        stem_metadata = read_stem_metadata(job.artifacts.stems_metadata_json)
        source_audio_path = source.relative_to(job.root).as_posix()

        job.melody.status = MelodyStatus.DETECTING
        job.melody.stage = "preparing_fusion_audio"
        job.melody.progress = 10
        await self._ensure_vocals_mono_16000(job, source)

        job.melody.stage = "extracting_pitch_csv"
        job.melody.progress = 20
        backend_statuses = await self._extract_backend_csvs(job)
        diagnostics["backends"] = backend_statuses
        succeeded = [backend for backend, status in backend_statuses.items() if status["status"] == "succeeded"]
        failed = {
            backend: status
            for backend, status in backend_statuses.items()
            if status["status"] == "failed"
        }
        diagnostics["succeeded_backends"] = succeeded
        diagnostics["failed_backends"] = failed
        diagnostics["missing_backends"] = [
            backend for backend, status in backend_statuses.items() if status["status"] == "missing"
        ]

        if len(succeeded) < MIN_SUCCESSFUL_BACKENDS:
            diagnostics.update(
                {
                    "fusion_status": "failed",
                    "failed_reason": "not_enough_successful_backends",
                    "required_min_successful_backends": MIN_SUCCESSFUL_BACKENDS,
                }
            )
            save_diagnostics(job.artifacts.melody_fusion_diagnostics_json, diagnostics)
            raise AppError(
                500,
                "MELODY_FUSION_FAILED",
                "adaptive fusion 失敗：成功的 pitch backend 少於 2 個。",
                True,
            )

        job.melody.stage = "fusing_pitch"
        job.melody.progress = 65
        paths = {
            backend: job.artifacts.melody_fusion_input_csv(backend)
            for backend in succeeded
        }
        try:
            fusion_payload = fuse_pitch_csvs(
                rmvpe_csv=paths.get("rmvpe"),
                torchcrepe_csv=paths.get("torchcrepe"),
                fcpe_csv=paths.get("fcpe"),
                pesto_csv=paths.get("pesto"),
                output_json_path=job.artifacts.melody_fusion_json,
                output_csv_path=job.artifacts.melody_fusion_csv,
                config=FusionConfig(em_iterations=2),
            )
        except Exception as exc:
            diagnostics.update(
                {
                    "fusion_status": "failed",
                    "failed_reason": "fusion_exception",
                    "error": str(exc)[:500],
                    "required_min_successful_backends": MIN_SUCCESSFUL_BACKENDS,
                }
            )
            save_diagnostics(job.artifacts.melody_fusion_diagnostics_json, diagnostics)
            raise AppError(500, "MELODY_FUSION_FAILED", "adaptive fusion 計算失敗。", True) from exc

        diagnostics.update(
            {
                "fusion_status": "succeeded",
                "required_min_successful_backends": MIN_SUCCESSFUL_BACKENDS,
                "fusion": fusion_payload.get("fusion", {}),
                "warnings": fusion_payload.get("fusion", {}).get("warnings", []),
            }
        )
        self._try_postprocess_fusion(job, diagnostics)
        save_diagnostics(job.artifacts.melody_fusion_diagnostics_json, diagnostics)

        job.melody.status = MelodyStatus.EXPORTING
        job.melody.stage = "exporting"
        job.melody.progress = 80
        try:
            analyze_fusion_melody(
                source,
                job.artifacts.melody_fusion_json,
                temporary_json,
                temporary_midi,
                job_id=job.job_id,
                key=job.analysis.display_name,
                root_index=job.analysis.root_index,
                mode=job.analysis.mode,
                meter_hint=meter_hint,
                min_note_duration_sec=self.settings.melody_min_note_duration_sec,
                max_gap_merge_sec=self.settings.melody_max_gap_merge_sec,
                min_confidence=self.settings.melody_min_confidence,
                max_notes=self.settings.melody_max_notes,
                requested_source=requested_source,
                melody_source_used=source_used,
                source_audio_path=source_audio_path,
                beat_reference=job.artifacts.analysis_audio,
                separation_backend=stem_metadata.backend if stem_metadata else None,
                separation_status=stem_metadata.status if stem_metadata else "missing",
            )
            result = MelodyAnalysisResult.model_validate_json(
                temporary_json.read_text(encoding="utf-8")
            )
            temporary_json.replace(job.artifacts.melody_variant_json(source_used))
            temporary_midi.replace(job.artifacts.melody_variant_midi(source_used))
            sync_best_melody_alias(job, tuple(self.settings.melody_source_priority.split(",")))
        except Exception as exc:
            temporary_json.unlink(missing_ok=True)
            temporary_midi.unlink(missing_ok=True)
            diagnostics["fusion_status"] = "failed"
            diagnostics["failed_reason"] = "melody_export_exception"
            diagnostics["error"] = str(exc)[:500]
            save_diagnostics(job.artifacts.melody_fusion_diagnostics_json, diagnostics)
            raise AppError(500, "MELODY_EXPORT_FAILED", "主旋律檔案輸出失敗。", True) from exc
        return result

    def _try_postprocess_fusion(self, job: Job, diagnostics: dict[str, Any]) -> None:
        try:
            postprocess_melody_fusion_artifacts(
                PostprocessArtifacts(
                    comparison_csv=job.artifacts.melody_comparison_csv,
                    fusion_csv=job.artifacts.melody_fusion_csv,
                    diagnostics_json=job.artifacts.melody_fusion_diagnostics_json,
                    output_csv=job.artifacts.melody_postprocessed_csv,
                    output_json=job.artifacts.melody_postprocessed_json,
                    output_diagnostics_json=job.artifacts.melody_postprocess_diagnostics_json,
                    backend_csvs={
                        backend: job.artifacts.melody_fusion_input_csv(backend)
                        for backend in BACKENDS
                    },
                )
            )
            diagnostics["postprocess_status"] = "succeeded"
            diagnostics["postprocessed_artifacts"] = {
                "csv": str(job.artifacts.melody_postprocessed_csv),
                "json": str(job.artifacts.melody_postprocessed_json),
                "diagnostics_json": str(job.artifacts.melody_postprocess_diagnostics_json),
            }
        except Exception as exc:
            diagnostics["postprocess_status"] = "failed"
            diagnostics["postprocess_warning"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            warnings = diagnostics.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append("postprocess_failed")

    async def _ensure_vocals_mono_16000(self, job: Job, source: Path) -> Path:
        output = job.artifacts.vocals_mono_16000_wav
        if output.exists() and output.stat().st_mtime >= source.stat().st_mtime:
            return output
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            await run_process(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ],
                timeout=min(self.settings.melody_timeout_seconds, 300),
            )
            await probe_audio(output, self.settings)
        except ProcessTimedOut as exc:
            output.unlink(missing_ok=True)
            raise AppError(504, "MELODY_PROCESS_TIMEOUT", "人聲音訊準備超過時間限制。", True) from exc
        except ProcessFailed as exc:
            output.unlink(missing_ok=True)
            raise AppError(500, "MELODY_AUDIO_PREP_FAILED", "無法準備 fusion 人聲音訊。", True) from exc
        return output

    async def _extract_backend_csvs(self, job: Job) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        for backend in BACKENDS:
            output = job.artifacts.melody_fusion_input_csv(backend)
            if output.exists():
                statuses[backend] = summarize_pitch_csv(output, backend=backend)
                continue
            try:
                if backend == "rmvpe":
                    rows = await self._extract_rmvpe_fusion_rows(job)
                    write_pitch_csv(output, rows)
                else:
                    await self._run_backend_script(backend, job.artifacts.vocals_mono_16000_wav, output)
                statuses[backend] = summarize_pitch_csv(output, backend=backend)
            except Exception as exc:
                output.unlink(missing_ok=True)
                statuses[backend] = {
                    "backend": backend,
                    "status": "failed",
                    "input_path": str(output),
                    "rows": 0,
                    "confidence_kind": "none",
                    "voiced_ratio": 0.0,
                    "failed_reason": str(exc).replace(str(job.root), "<job>")[:500],
                }
        return statuses

    async def _extract_rmvpe_fusion_rows(self, job: Job) -> list[dict[str, Any]]:
        if not self.settings.rmvpe_python.is_file():
            raise RuntimeError("The configured RMVPE Python executable is unavailable.")
        script = Path(__file__).resolve().parents[3] / "scripts" / "run_rmvpe_pitch.py"
        temporary_json = job.artifacts.melody_fusion_dir / ".rmvpe_fusion_pitch.json.tmp"
        temporary_json.unlink(missing_ok=True)
        command = [
            str(self.settings.rmvpe_python),
            str(script),
            str(job.artifacts.vocals_mono_16000_wav),
            str(temporary_json),
            "--voiced-confidence-threshold",
            str(self.settings.rmvpe_voiced_confidence_threshold),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=build_gpu_subprocess_env(mode="onnx", gpu_python=self.settings.rmvpe_python),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.settings.rmvpe_timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            temporary_json.unlink(missing_ok=True)
            raise RuntimeError("rmvpe pitch extraction timed out") from exc
        if process.returncode:
            text = (stderr or stdout).decode("utf-8", errors="replace").strip()
            temporary_json.unlink(missing_ok=True)
            raise RuntimeError(f"rmvpe pitch extraction failed: {text[-500:]}")
        try:
            return pitch_csv_rows_from_rmvpe_json(temporary_json)
        finally:
            temporary_json.unlink(missing_ok=True)

    async def _run_backend_script(self, backend: str, input_wav: Path, output_csv: Path) -> None:
        if not self.settings.rmvpe_python.is_file():
            raise RuntimeError("The configured GPU Python executable is unavailable.")
        script_name = {
            "torchcrepe": "run_torchcrepe_pitch_csv.py",
            "fcpe": "run_fcpe_pitch_csv.py",
            "pesto": "run_pesto_pitch_csv.py",
        }[backend]
        script = Path(__file__).resolve().parents[3] / "scripts" / script_name
        command = [str(self.settings.rmvpe_python), str(script), str(input_wav), str(output_csv)]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=build_gpu_subprocess_env(mode="torch", gpu_python=self.settings.rmvpe_python),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.settings.melody_timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError(f"{backend} pitch extraction timed out") from exc
        if process.returncode:
            text = (stderr or stdout).decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"{backend} pitch extraction failed: {text[-500:]}")
        if not output_csv.exists():
            raise RuntimeError(f"{backend} pitch extraction did not create CSV")

    def _base_diagnostics(self) -> dict[str, Any]:
        return {
            "fusion_status": "pending",
            "required_min_successful_backends": MIN_SUCCESSFUL_BACKENDS,
            "succeeded_backends": [],
            "missing_backends": [],
            "failed_backends": {},
            "warnings": [],
        }
