import pytest

from app.services.key_names import display_key, shift_options


def test_wraps_across_octave():
    assert display_key(-1, "major") == "B Major"
    assert display_key(12, "minor") == "C Minor"


@pytest.mark.parametrize("root", range(12))
@pytest.mark.parametrize("mode", ["major", "minor"])
def test_all_shift_options(root, mode):
    options = shift_options(root, mode, 3)
    assert [item.semitones for item in options] == list(range(-3, 4))
    assert options[3].target_key == display_key(root, mode)

