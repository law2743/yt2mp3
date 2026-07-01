"""Public model exports kept stable for API and test compatibility."""

from app.models.api import AnalyzeRequest, MelodyRequest, StemRequest, TransposeRequest
from app.models.job import (
    ErrorDetail,
    JobPublic,
    JobStatus,
    NotationArtifactsInfo,
    OutputInfo,
    SourceInfo,
)
from app.models.music import KeyAnalysisResult, KeyCandidate, ShiftOption
from app.models.melody import MelodyAnalysisResult, MelodyStatus
from app.models.stem import StemSeparationMetadata, StemTaskStatus

__all__ = [
    "AnalyzeRequest",
    "ErrorDetail",
    "JobPublic",
    "JobStatus",
    "KeyAnalysisResult",
    "KeyCandidate",
    "MelodyAnalysisResult",
    "MelodyRequest",
    "MelodyStatus",
    "NotationArtifactsInfo",
    "OutputInfo",
    "ShiftOption",
    "SourceInfo",
    "StemRequest",
    "StemSeparationMetadata",
    "StemTaskStatus",
    "TransposeRequest",
]
