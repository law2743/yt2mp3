# Third-party notices

This application invokes or depends directly on the following projects. The
exact Python package versions are recorded in `requirements.txt`.

- Rubber Band Library and command-line utility — GPL-2.0-or-later; commercial
  licensing is also available from Breakfast Quay.
- FFmpeg and ffprobe — LGPL-2.1-or-later by default; actual obligations depend
  on the build configuration and enabled codecs. The Docker image uses Debian's
  packaged build.
- yt-dlp — The Unlicense.
- librosa — ISC License.
- FastAPI — MIT License.
- Uvicorn — BSD-3-Clause License.
- NumPy — BSD-3-Clause License.
- SciPy — BSD-3-Clause License.
- scikit-learn — BSD-3-Clause License.
- Pillow — HPND License.
- SoundFile — BSD-3-Clause License; it loads libsndfile, which is LGPL-2.1-or-later.

This list is an engineering notice, not legal advice. Redistributors must review
the licenses shipped with the exact dependency and system-package versions they
distribute.

