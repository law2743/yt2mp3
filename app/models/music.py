from typing import Literal

from pydantic import BaseModel, Field, computed_field


class KeyCandidate(BaseModel):
    key: str
    score: float = Field(ge=0, le=1)


class KeyAnalysisResult(BaseModel):
    root_index: int = Field(ge=0, le=11)
    root_name: str
    mode: Literal["major", "minor"]
    display_name: str
    confidence: float = Field(ge=0, le=1)
    candidates: list[KeyCandidate] = Field(max_length=3)
    algorithm_version: str

    @computed_field
    @property
    def key(self) -> str:
        return self.display_name


class ShiftOption(BaseModel):
    semitones: int
    label: str
    target_key: str
