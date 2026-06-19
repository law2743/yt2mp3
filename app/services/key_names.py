from __future__ import annotations

from app.models import ShiftOption

FLAT_NOTES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")


def display_key(root_index: int, mode: str) -> str:
    suffix = "Major" if mode == "major" else "Minor"
    return f"{FLAT_NOTES[root_index % 12]} {suffix}"


def shift_options(root_index: int, mode: str, shift_range: int) -> list[ShiftOption]:
    options = []
    for semitones in range(-shift_range, shift_range + 1):
        if semitones < 0:
            label = f"降 {abs(semitones)} 半音"
        elif semitones > 0:
            label = f"升 {semitones} 半音"
        else:
            label = "原調"
        options.append(
            ShiftOption(
                semitones=semitones,
                label=label,
                target_key=display_key(root_index + semitones, mode),
            )
        )
    return options

