import pytest

from app.services.files import safe_child, sanitize_filename
from app.services.artifacts import JobArtifacts


def test_sanitize_filename_removes_path_and_shell_characters():
    value = sanitize_filename("../bad/$(`name`); song?.mp3")
    assert "/" not in value
    assert "`" not in value
    assert "$" not in value
    assert len(value) <= 120


def test_sanitize_filename_has_fallback_and_limit():
    assert sanitize_filename("../") == "audio"
    assert len(sanitize_filename("a" * 200)) == 120


def test_safe_child_rejects_escape(tmp_path):
    with pytest.raises(ValueError):
        safe_child(tmp_path, "..", "secret")


def test_job_artifacts_create_stable_layout(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()

    assert artifacts.source_dir.is_dir()
    assert artifacts.analysis_dir.is_dir()
    assert artifacts.output_dir.is_dir()
    assert artifacts.analysis_audio == tmp_path / "job-id" / "analysis" / "mono-22050.wav"
    assert artifacts.transposed_mp3(-2, 192).name == "shift_-2_192k.mp3"
