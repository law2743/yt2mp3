from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.services.gpu_subprocess_env import build_gpu_subprocess_env


def test_torch_mode_removes_pythonpath_and_ld_library_path(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/foreign/python")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/foreign/cudnn")

    environment = build_gpu_subprocess_env(
        mode="torch",
        gpu_python=Path("/gpu/bin/python"),
    )

    assert "PYTHONPATH" not in environment
    assert "LD_LIBRARY_PATH" not in environment


def test_onnx_mode_uses_gpu_python_nvidia_libraries(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/foreign/python")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/old/conda/nvidia/cudnn/lib")
    discovered = [
        "/gpu/venv/lib/python3.12/site-packages/nvidia/cublas/lib",
        "/gpu/venv/lib/python3.12/site-packages/nvidia/cuda_runtime/lib",
        "/gpu/venv/lib/python3.12/site-packages/nvidia/cudnn/lib",
    ]
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(discovered),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    environment = build_gpu_subprocess_env(
        mode="onnx",
        gpu_python=Path("/gpu/bin/python"),
    )

    assert captured["args"][0][0] == "/gpu/bin/python"
    discovery_env = captured["kwargs"]["env"]
    assert "PYTHONPATH" not in discovery_env
    assert "LD_LIBRARY_PATH" not in discovery_env
    assert "PYTHONPATH" not in environment
    assert environment["LD_LIBRARY_PATH"].split(":") == discovered
    assert "nvidia/cublas/lib" in environment["LD_LIBRARY_PATH"]
    assert "nvidia/cuda_runtime/lib" in environment["LD_LIBRARY_PATH"]
    assert "nvidia/cudnn/lib" in environment["LD_LIBRARY_PATH"]
    assert "/old/conda/nvidia/cudnn/lib" not in environment["LD_LIBRARY_PATH"]
