from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from app.models import KeyAnalysisResult, KeyCandidate
from app.services.key_names import display_key

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


class KeyAnalyzer(Protocol):
    def analyze(self, audio_path: Path) -> KeyAnalysisResult: ...


class LibrosaKeyAnalyzer:
    algorithm_version = "librosa-chroma-stft-ks-v1"

    def analyze(self, audio_path: Path) -> KeyAnalysisResult:
        import librosa

        audio, sample_rate = librosa.load(audio_path, sr=22050, mono=True)
        if audio.size < sample_rate * 2:
            raise ValueError("audio is too short")
        audio, _ = librosa.effects.trim(audio, top_db=35)
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        if audio.size < sample_rate * 2 or rms < 1e-4:
            raise ValueError("audio is silent or too short")

        harmonic = librosa.effects.harmonic(audio)
        # chroma_stft is intentionally used instead of chroma_cqt. The latter
        # enters a numba gufunc during tuning estimation that is unstable on
        # some macOS arm64/Python 3.12 combinations. STFT chroma is deterministic,
        # lighter on Render CPU, and sufficient for the global key profile.
        chroma = librosa.feature.chroma_stft(
            y=harmonic, sr=sample_rate, n_fft=4096, hop_length=512, tuning=0.0
        )
        frame_rms = librosa.feature.rms(y=harmonic, frame_length=2048, hop_length=512)[0]
        if frame_rms.size != chroma.shape[1]:
            frame_rms = np.interp(
                np.linspace(0, 1, chroma.shape[1]),
                np.linspace(0, 1, frame_rms.size),
                frame_rms,
            )
        profile = np.average(chroma, axis=1, weights=np.maximum(frame_rms, 1e-8))
        if not np.isfinite(profile).all() or np.linalg.norm(profile) < 1e-6:
            raise ValueError("audio has no stable pitch profile")

        scored: list[tuple[float, int, str]] = []
        for mode, template in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            for root in range(12):
                score = float(np.corrcoef(profile, np.roll(template, root))[0, 1])
                scored.append((score, root, mode))
        scored.sort(reverse=True)

        # Softmax produces a stable relative UI indicator, not a statistical probability.
        top = scored[:3]
        logits = np.array([item[0] for item in top]) * 5.0
        weights = np.exp(logits - logits.max())
        weights /= weights.sum()
        margin = max(0.0, top[0][0] - top[1][0])
        confidence = float(np.clip(0.25 + margin * 1.8, 0.05, 0.95))
        best_score, root, mode = top[0]
        _ = best_score
        candidates = [
            KeyCandidate(key=display_key(item[1], item[2]), score=round(float(weight), 4))
            for item, weight in zip(top, weights, strict=True)
        ]
        return KeyAnalysisResult(
            root_index=root,
            root_name=display_key(root, mode).split()[0],
            mode=mode,
            display_name=display_key(root, mode),
            confidence=round(confidence, 4),
            candidates=candidates,
            algorithm_version=self.algorithm_version,
        )
