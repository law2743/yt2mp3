from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048, strict=True)


class TransposeRequest(BaseModel):
    semitones: int = Field(strict=True)
    bitrate_kbps: Literal[128, 192, 256] = 192
