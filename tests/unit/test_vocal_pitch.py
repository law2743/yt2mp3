from __future__ import annotations

import json

import numpy as np

from scripts.run_rmvpe_pitch import _write_output


def test_rmvpe_script_output_schema_uses_confidence_threshold(tmp_path):
    output = tmp_path / "vocal_pitch.json"
    _write_output(
        output,
        sample_rate=44100,
        duration_seconds=1.0,
        threshold=0.03,
        time=np.asarray([0.0, 0.01]),
        frequency=np.asarray([440.0, 220.0]),
        confidence=np.asarray([0.02, 0.03]),
        activation=np.zeros((2, 360)),
    )

    result = json.loads(output.read_text(encoding="utf-8"))

    assert result["schema_version"] == "vocal_pitch.v1"
    assert result["backend"] == "rmvpe_onnx"
    assert result["fallback_used"] is False
    assert result["voiced_confidence_threshold"] == 0.03
    assert result["points"][0]["frequency_hz"] == 440.0
    assert result["points"][0]["midi"] is not None
    assert result["points"][0]["voiced"] is False
    assert result["points"][1]["voiced"] is True
    assert result["metadata"]["activation_shape"] == [2, 360]
