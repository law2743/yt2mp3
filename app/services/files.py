from __future__ import annotations

import re
import unicodedata
from pathlib import Path

UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|$`;&]+")


def safe_child(root: Path, *parts: str) -> Path:
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError("path escapes job root")
    return candidate


def sanitize_filename(value: str, max_length: int = 120) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = UNSAFE_FILENAME.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    if not value:
        value = "audio"
    return value[:max_length].rstrip(" ._") or "audio"

