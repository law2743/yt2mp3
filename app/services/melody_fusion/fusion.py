from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import build_output_json, load_pitch_csv, save_diagnostics, save_fusion_csv, save_json
from .schema import Candidate, FusionDiagnostics, PitchPoint, PitchTrack
from .utils import cents_distance, estimate_note_segments, hz_to_midi, midi_to_hz, round_time


@dataclass(frozen=True)
class FusionConfig:
    """Config for adaptive melody fusion.

    The defaults are conservative for 10 ms vocal pitch frames.
    """

    cluster_cents: float = 50.0
    agreement_cents: float = 50.0
    octave_cents_tolerance: float = 60.0
    min_frequency_hz: float = 70.0
    max_frequency_hz: float = 1100.0
    voiced_threshold: float = 0.35
    unvoiced_bias: float = 0.30
    transition_jump_weight: float = 0.020
    transition_octave_penalty: float = 0.35
    transition_voicing_penalty: float = 0.22
    min_voiced_ms: float = 60.0
    min_unvoiced_ms: float = 50.0
    em_iterations: int = 2
    timeline_mode: str = "intersection"  # intersection | union | reference:rmvpe
    reference_backend: str | None = None
    include_raw_unvoiced_candidates: bool = False


def fuse_pitch_csvs(
    *,
    rmvpe_csv: str | Path | None = None,
    torchcrepe_csv: str | Path | None = None,
    fcpe_csv: str | Path | None = None,
    pesto_csv: str | Path | None = None,
    output_json_path: str | Path | None = None,
    output_csv_path: str | Path | None = None,
    diagnostics_path: str | Path | None = None,
    config: FusionConfig | None = None,
) -> dict[str, Any]:
    """Fuse backend pitch CSVs and return an adaptive_fusion JSON payload.

    This function does not import torch/onnxruntime/demucs/RMVPE. It only reads
    already-created pitch CSV files and writes lightweight fusion artifacts.
    """
    cfg = config or FusionConfig()
    track_specs = {
        "rmvpe": rmvpe_csv,
        "torchcrepe": torchcrepe_csv,
        "fcpe": fcpe_csv,
        "pesto": pesto_csv,
    }
    tracks = [load_pitch_csv(p, backend=backend) for backend, p in track_specs.items() if p]
    if len(tracks) < 2:
        raise ValueError("Need at least two pitch CSVs to run adaptive melody fusion")

    timeline, step = _build_timeline(tracks, cfg)
    if not timeline:
        raise ValueError("No common timeline could be built from input pitch CSVs")

    # Time-indexed lookup for each backend. Input files are 10 ms aligned; rounded keys are enough.
    indices = {tr.backend: {round_time(pt.time_sec): pt for pt in tr.frames} for tr in tracks}
    weights = {tr.backend: _initial_model_weight(tr) for tr in tracks}
    warnings = _detect_warnings(tracks, cfg)
    best_path: list[Candidate] = []

    for _ in range(max(cfg.em_iterations, 1)):
        lattice = [_frame_candidates(t, tracks, indices, weights, cfg) for t in timeline]
        best_path = _viterbi(lattice, cfg)
        weights = _update_model_weights(best_path, tracks, indices, timeline, cfg)

    if not best_path:
        raise RuntimeError("Viterbi produced an empty melody path")

    voiced, f0s, confidences, agreements, support_counts = _path_to_series(best_path, cfg)
    f0s, voiced = _remove_short_events(timeline, f0s, voiced, cfg)

    frames: list[dict[str, Any]] = []
    for t, f0, is_voiced, conf, agreement, support_count in zip(timeline, f0s, voiced, confidences, agreements, support_counts):
        raw_f0 = float(f0) if f0 > 0 else 0.0
        frames.append(
            {
                "time_sec": round_time(t),
                "f0_hz": round(float(f0), 6) if is_voiced else 0.0,
                "raw_f0_hz": round(raw_f0, 6),
                "confidence": round(float(conf), 6),
                "agreement": round(float(agreement), 6),
                "support_count": int(support_count),
                "midi": round(hz_to_midi(float(f0)), 4) if is_voiced and f0 > 0 else 0.0,
                "voiced": bool(is_voiced),
                "backend": "adaptive_fusion",
            }
        )

    diagnostics = FusionDiagnostics(
        input_backends=[tr.backend for tr in tracks],
        input_frame_counts={tr.backend: tr.frame_count for tr in tracks},
        input_durations_sec={tr.backend: round(tr.duration_sec, 6) for tr in tracks},
        output_frame_count=len(frames),
        output_duration_sec=round(timeline[-1], 6),
        timeline_mode=cfg.timeline_mode,
        model_weights={k: round(v, 6) for k, v in sorted(weights.items())},
        warnings=warnings,
    )

    sample_rate = _choose_common_meta(tracks, "sample_rate")
    hop_size = _choose_common_meta(tracks, "hop_size")
    payload = build_output_json(
        frames=frames,
        diagnostics=diagnostics.to_dict(),
        sample_rate=sample_rate,
        hop_size=hop_size,
        frame_step_sec=step,
        confidence_threshold=cfg.voiced_threshold,
    )

    segments = estimate_note_segments(
        [fr["time_sec"] for fr in frames],
        [float(fr["f0_hz"]) for fr in frames],
        [bool(fr["voiced"]) for fr in frames],
    )
    payload["fusion"]["segments_count"] = len(segments)

    if output_json_path:
        save_json(output_json_path, payload)
    if output_csv_path:
        save_fusion_csv(output_csv_path, frames)
    if diagnostics_path:
        diagnostics_payload = {
            "fusion_status": "succeeded",
            "required_min_successful_backends": 2,
            "succeeded_backends": [tr.backend for tr in tracks],
            "missing_backends": [name for name, path in track_specs.items() if not path],
            "failed_backends": {},
            "warnings": warnings,
            "inputs": {
                tr.backend: {
                    "input_path": tr.source_path,
                    "rows": tr.frame_count,
                    "confidence_kind": tr.original.get("confidence_kind", "none"),
                    "missing_confidence_rows": sum(1 for pt in tr.frames if pt.raw_confidence is None),
                    "voiced_ratio": round(
                        sum(1 for pt in tr.frames if pt.voiced) / tr.frame_count, 6
                    )
                    if tr.frame_count
                    else 0.0,
                }
                for tr in tracks
            },
            "fusion": diagnostics.to_dict(),
        }
        save_diagnostics(diagnostics_path, diagnostics_payload)
    return payload


def _initial_model_weight(track: PitchTrack) -> float:
    return 1.0


def _build_timeline(tracks: list[PitchTrack], cfg: FusionConfig) -> tuple[list[float], float]:
    steps = [tr.frame_step_sec for tr in tracks if tr.frame_step_sec > 0]
    step = sorted(steps)[len(steps) // 2] if steps else 0.01
    if cfg.reference_backend:
        ref = next((tr for tr in tracks if tr.backend == cfg.reference_backend), None)
        if ref is None:
            raise ValueError(f"reference_backend={cfg.reference_backend!r} not found")
        return [pt.time_sec for pt in ref.frames], ref.frame_step_sec
    if cfg.timeline_mode.startswith("reference:"):
        backend = cfg.timeline_mode.split(":", 1)[1]
        ref = next((tr for tr in tracks if tr.backend == backend), None)
        if ref is None:
            raise ValueError(f"timeline reference backend={backend!r} not found")
        return [pt.time_sec for pt in ref.frames], ref.frame_step_sec

    start = max((tr.frames[0].time_sec for tr in tracks if tr.frames), default=0.0)
    if cfg.timeline_mode == "union":
        end = max((tr.duration_sec for tr in tracks), default=0.0)
    else:
        end = min((tr.duration_sec for tr in tracks), default=0.0)
    count = int(round((end - start) / step)) + 1
    timeline = [round_time(start + i * step) for i in range(max(count, 0))]
    return timeline, step


def _frame_candidates(
    time_sec: float,
    tracks: list[PitchTrack],
    indices: dict[str, dict[float, PitchPoint]],
    weights: dict[str, float],
    cfg: FusionConfig,
) -> list[Candidate]:
    points: list[PitchPoint] = []
    for tr in tracks:
        pt = indices[tr.backend].get(round_time(time_sec))
        if pt is None:
            continue
        if pt.voiced and cfg.min_frequency_hz <= pt.f0_hz <= cfg.max_frequency_hz:
            points.append(pt)
        elif cfg.include_raw_unvoiced_candidates and cfg.min_frequency_hz <= pt.raw_f0_hz <= cfg.max_frequency_hz:
            points.append(pt)

    clusters = _cluster_points(points, weights, cfg, total_model_count=len(tracks))
    missing_or_unvoiced = max(len(tracks) - len(points), 0)
    unvoiced_score = cfg.unvoiced_bias + (missing_or_unvoiced / max(len(tracks), 1)) * 0.55
    candidates = [
        Candidate(
            f0_hz=0.0,
            voiced=False,
            emission=float(unvoiced_score),
            agreement=1.0 if not points else missing_or_unvoiced / max(len(tracks), 1),
            support_count=missing_or_unvoiced,
            support_backends=tuple(),
            source_f0s={},
        )
    ]
    candidates.extend(clusters)
    # Keep only the strongest few candidates to prevent rare noisy models from expanding the lattice.
    voiced_candidates = sorted([c for c in candidates if c.voiced], key=lambda c: c.emission, reverse=True)[:5]
    return [candidates[0], *voiced_candidates]


def _cluster_points(points: list[PitchPoint], weights: dict[str, float], cfg: FusionConfig, *, total_model_count: int) -> list[Candidate]:
    clusters: list[list[PitchPoint]] = []
    for pt in sorted(points, key=lambda x: x.f0_hz):
        placed = False
        for cluster in clusters:
            center = _weighted_center_hz(cluster, weights)
            if cents_distance(pt.f0_hz, center) <= cfg.cluster_cents:
                cluster.append(pt)
                placed = True
                break
        if not placed:
            clusters.append([pt])

    candidates: list[Candidate] = []
    total_models = max(total_model_count, 1)
    for cluster in clusters:
        f0_hz = _weighted_center_hz(cluster, weights)
        support_backends = tuple(sorted({pt.backend for pt in cluster}))
        support_count = len(support_backends)
        raw_support = sum((pt.normalized_confidence + 0.15) * weights.get(pt.backend, 1.0) for pt in cluster)
        agreement = support_count / total_models
        # Boost candidates that multiple independent models agree on.
        emission = raw_support * (0.70 + 0.45 * agreement)
        candidates.append(
            Candidate(
                f0_hz=f0_hz,
                voiced=True,
                emission=float(emission),
                agreement=float(agreement),
                support_count=support_count,
                support_backends=support_backends,
                source_f0s={pt.backend: pt.f0_hz for pt in cluster},
            )
        )
    return candidates


def _weighted_center_hz(points: list[PitchPoint], weights: dict[str, float]) -> float:
    # Weighted average in MIDI space is more stable for pitch than Hz averaging.
    total = 0.0
    acc = 0.0
    for pt in points:
        w = max((pt.normalized_confidence + 0.10) * weights.get(pt.backend, 1.0), 1e-6)
        acc += hz_to_midi_safe(pt.f0_hz) * w
        total += w
    return midi_to_hz(acc / total) if total > 0 else 0.0


def hz_to_midi_safe(f0_hz: float) -> float:
    from .utils import hz_to_midi

    return hz_to_midi(max(f0_hz, 1e-9))


def _viterbi(lattice: list[list[Candidate]], cfg: FusionConfig) -> list[Candidate]:
    if not lattice:
        return []
    dp: list[list[float]] = []
    back: list[list[int]] = []
    dp.append([cand.emission for cand in lattice[0]])
    back.append([-1 for _ in lattice[0]])

    for i in range(1, len(lattice)):
        row_scores: list[float] = []
        row_back: list[int] = []
        for cand in lattice[i]:
            best_score = float("-inf")
            best_j = 0
            for j, prev in enumerate(lattice[i - 1]):
                score = dp[i - 1][j] + cand.emission - _transition_cost(prev, cand, cfg)
                if score > best_score:
                    best_score = score
                    best_j = j
            row_scores.append(best_score)
            row_back.append(best_j)
        dp.append(row_scores)
        back.append(row_back)

    last_idx = max(range(len(dp[-1])), key=lambda j: dp[-1][j])
    path: list[Candidate] = []
    for i in range(len(lattice) - 1, -1, -1):
        path.append(lattice[i][last_idx])
        last_idx = back[i][last_idx]
        if last_idx < 0 and i > 0:
            last_idx = 0
    path.reverse()
    return path


def _transition_cost(prev: Candidate, cur: Candidate, cfg: FusionConfig) -> float:
    if prev.voiced != cur.voiced:
        return cfg.transition_voicing_penalty
    if not prev.voiced and not cur.voiced:
        return 0.0
    if prev.f0_hz <= 0 or cur.f0_hz <= 0:
        return cfg.transition_voicing_penalty
    dist = cents_distance(prev.f0_hz, cur.f0_hz)
    cost = min(dist * cfg.transition_jump_weight / 100.0, 1.75)
    # Explicitly discourage one-frame octave flips, a common pitch-tracking error.
    octave_dist = abs(dist - 1200.0)
    if octave_dist <= cfg.octave_cents_tolerance:
        cost += cfg.transition_octave_penalty
    return cost


def _update_model_weights(
    path: list[Candidate],
    tracks: list[PitchTrack],
    indices: dict[str, dict[float, PitchPoint]],
    timeline: list[float],
    cfg: FusionConfig,
) -> dict[str, float]:
    scores: dict[str, list[float]] = {tr.backend: [] for tr in tracks}
    for cand, t in zip(path, timeline):
        if not cand.voiced or cand.f0_hz <= 0:
            continue
        for tr in tracks:
            pt = indices[tr.backend].get(round_time(t))
            if not pt or not pt.voiced or pt.f0_hz <= 0:
                continue
            dist = cents_distance(pt.f0_hz, cand.f0_hz)
            if dist <= cfg.agreement_cents:
                match = 1.0
            elif abs(dist - 1200.0) <= cfg.octave_cents_tolerance:
                match = 0.35
            else:
                match = 0.0
            scores[tr.backend].append(match * (0.35 + 0.65 * pt.normalized_confidence))
    weights: dict[str, float] = {}
    for tr in tracks:
        vals = scores[tr.backend]
        if not vals:
            weights[tr.backend] = 0.35
            continue
        reliability = sum(vals) / len(vals)
        # Bound weights to prevent the first pass consensus from permanently muting a model.
        weights[tr.backend] = min(max(0.35 + 0.90 * reliability, 0.35), 1.25)
    return weights


def _path_to_series(path: list[Candidate], cfg: FusionConfig) -> tuple[list[bool], list[float], list[float], list[float], list[int]]:
    voiced: list[bool] = []
    f0s: list[float] = []
    confidences: list[float] = []
    agreements: list[float] = []
    support_counts: list[int] = []
    max_emission = max((c.emission for c in path), default=1.0)
    for cand in path:
        conf = cand.emission / max(max_emission, 1e-6)
        is_voiced = bool(cand.voiced and cand.f0_hz > 0 and conf >= cfg.voiced_threshold and (cand.support_count >= 2 or conf >= 0.75))
        voiced.append(is_voiced)
        f0s.append(float(cand.f0_hz if is_voiced else 0.0))
        confidences.append(float(min(max(conf, 0.0), 1.0)))
        agreements.append(float(cand.agreement))
        support_counts.append(int(cand.support_count))
    return voiced, f0s, confidences, agreements, support_counts


def _remove_short_events(timeline: list[float], f0s: list[float], voiced: list[bool], cfg: FusionConfig) -> tuple[list[float], list[bool]]:
    if not timeline:
        return f0s, voiced
    step = timeline[1] - timeline[0] if len(timeline) > 1 else 0.01
    min_voiced_frames = max(int(round((cfg.min_voiced_ms / 1000.0) / step)), 1)
    min_unvoiced_frames = max(int(round((cfg.min_unvoiced_ms / 1000.0) / step)), 1)
    out_f0s = list(f0s)
    out_voiced = list(voiced)

    start = 0
    while start < len(out_voiced):
        end = start + 1
        while end < len(out_voiced) and out_voiced[end] == out_voiced[start]:
            end += 1
        length = end - start
        if out_voiced[start] and length < min_voiced_frames:
            # Remove very short voiced islands unless they are between similar voiced neighbors.
            left = start - 1
            right = end
            if left >= 0 and right < len(out_voiced) and out_voiced[left] and out_voiced[right]:
                if cents_distance(max(out_f0s[left], 1e-9), max(out_f0s[right], 1e-9)) <= cfg.cluster_cents:
                    fill = (out_f0s[left] + out_f0s[right]) / 2.0
                    for i in range(start, end):
                        out_f0s[i] = fill
                        out_voiced[i] = True
                else:
                    for i in range(start, end):
                        out_f0s[i] = 0.0
                        out_voiced[i] = False
            else:
                for i in range(start, end):
                    out_f0s[i] = 0.0
                    out_voiced[i] = False
        elif (not out_voiced[start]) and length < min_unvoiced_frames:
            # Fill tiny gaps between voiced regions with interpolated pitch.
            left = start - 1
            right = end
            if left >= 0 and right < len(out_voiced) and out_voiced[left] and out_voiced[right]:
                if cents_distance(max(out_f0s[left], 1e-9), max(out_f0s[right], 1e-9)) <= cfg.cluster_cents * 2:
                    for i in range(start, end):
                        alpha = (i - start + 1) / (length + 1)
                        out_f0s[i] = out_f0s[left] * (1 - alpha) + out_f0s[right] * alpha
                        out_voiced[i] = True
        start = end
    return out_f0s, out_voiced


def _detect_warnings(tracks: list[PitchTrack], cfg: FusionConfig) -> list[str]:
    warnings: list[str] = []
    durations = [tr.duration_sec for tr in tracks if tr.duration_sec > 0]
    if durations and max(durations) - min(durations) > 0.5:
        warnings.append(
            "Input pitch CSV durations differ by more than 0.5s; default intersection timeline will use the overlapping region only. Check whether all four models used the same vocals.wav."
        )
    frame_steps = [tr.frame_step_sec for tr in tracks]
    if frame_steps and max(frame_steps) - min(frame_steps) > 0.002:
        warnings.append("Input pitch CSV frame steps differ; fusion timeline may need explicit --timeline reference:<backend>.")
    for tr in tracks:
        if tr.frames and all(pt.raw_confidence is None for pt in tr.frames[: min(1000, len(tr.frames))]):
            warnings.append(f"{tr.backend} has no confidence field; using f0 agreement and transition scoring only.")
    return warnings


def _choose_common_meta(tracks: list[PitchTrack], attr: str) -> int | None:
    values = [getattr(tr, attr) for tr in tracks if getattr(tr, attr) is not None]
    if not values:
        return None
    # Most common value.
    return max(set(values), key=values.count)
