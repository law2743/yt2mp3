# Third-party notices

This application invokes or depends directly on the following projects. The
exact Python package versions are recorded in `requirements.txt`,
`requirements-dev.txt`, and any GPU/model-specific requirements files used by
the local backend environment.

This list is an engineering notice, not legal advice. Redistributors must review
the licenses shipped with the exact dependency, model weight, and system-package
versions they distribute.

## Core application dependencies

* FastAPI — MIT License.
* Uvicorn — BSD-3-Clause License.
* NumPy — BSD-3-Clause License.
* SciPy — BSD-3-Clause License.
* scikit-learn — BSD-3-Clause License.
* Pillow — HPND License.
* Mido — MIT License.
* librosa — ISC License.
* SoundFile — BSD-3-Clause License; it loads libsndfile, which is LGPL-2.1-or-later.

## Audio and media processing dependencies

* Rubber Band Library and command-line utility — GPL-2.0-or-later; commercial
  licensing is also available from Breakfast Quay.
* FFmpeg and ffprobe — LGPL-2.1-or-later by default; actual obligations depend
  on the build configuration and enabled codecs. The Docker image uses Debian's
  packaged build.
* yt-dlp — The Unlicense.

## Optional source-separation and pitch-estimation backends

The project may optionally invoke local model backends for vocal separation,
pitch estimation, melody extraction, and adaptive melody fusion. These backends
may run in a separate local GPU environment and are not necessarily installed in
the lightweight web/API environment.

* Demucs — MIT License.
* RMVPE — Apache License 2.0.
* torchcrepe — MIT License. torchcrepe is a PyTorch implementation of the CREPE
  pitch tracker.
* FCPE — MIT License.
* PESTO — LGPL-3.0 License.

## Machine learning runtime dependencies

Depending on the enabled backend configuration, the local model environment may
also depend on the following projects:

* PyTorch — BSD-style License.
* torchaudio — BSD-style License.
* ONNX Runtime — MIT License.
* CUDA, cuDNN, and NVIDIA runtime components — proprietary NVIDIA licenses,
  when used in a GPU-enabled local environment.

## Model weights and checkpoints

Some pitch-estimation and source-separation backends may download or load
pretrained model weights. Model weights can have separate license terms from the
source code package. Redistributors must review the license and redistribution
terms for the exact checkpoints they ship, cache, or download automatically.

This project should not assume that a Python package license automatically covers
all model weights, checkpoints, datasets, or converted model files used by that
package.

## User-provided media

This application is intended for processing content that the user owns or is
authorized to process. It does not bypass DRM, private videos, regional
restrictions, or platform access controls.
