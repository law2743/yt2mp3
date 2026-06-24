from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal

GpuSubprocessMode = Literal["torch", "onnx"]


_NVIDIA_LIB_DISCOVERY_SCRIPT = r"""
import json
import sysconfig
from pathlib import Path

roots = []
for key in ("purelib", "platlib"):
    value = sysconfig.get_paths().get(key)
    if value:
        roots.append(Path(value))

paths = []
seen = set()
for root in roots:
    nvidia_root = root / "nvidia"
    if not nvidia_root.is_dir():
        continue
    for lib_dir in sorted(nvidia_root.glob("*/lib")):
        if not lib_dir.is_dir():
            continue
        resolved = str(lib_dir.resolve())
        if resolved not in seen:
            paths.append(resolved)
            seen.add(resolved)

print(json.dumps(paths))
"""


def _base_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("LD_LIBRARY_PATH", None)
    return environment


def _discover_nvidia_library_paths(gpu_python: Path) -> list[str]:
    completed = subprocess.run(
        [str(gpu_python), "-c", _NVIDIA_LIB_DISCOVERY_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
        env=_base_environment(),
    )
    paths = json.loads(completed.stdout)
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError("GPU Python returned invalid NVIDIA library path discovery output")
    return paths


def build_gpu_subprocess_env(
    mode: GpuSubprocessMode,
    gpu_python: str | Path,
) -> dict[str, str]:
    environment = _base_environment()
    if mode == "torch":
        return environment
    if mode == "onnx":
        library_paths = _discover_nvidia_library_paths(Path(gpu_python))
        if library_paths:
            environment["LD_LIBRARY_PATH"] = os.pathsep.join(library_paths)
        return environment
    raise ValueError(f"Unsupported GPU subprocess mode: {mode}")
