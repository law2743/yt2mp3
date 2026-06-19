import pytest

from app.services.files import safe_child, sanitize_filename


def test_sanitize_filename_removes_path_and_shell_characters():
    value = sanitize_filename('../bad/$(`name`); song?.mp3')
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

