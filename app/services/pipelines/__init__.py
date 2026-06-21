"""Application pipelines composed from replaceable service adapters."""

from app.services.pipelines.analyze import AnalyzePipeline
from app.services.pipelines.melody import MelodyPipeline
from app.services.pipelines.transpose import TransposePipeline

__all__ = ["AnalyzePipeline", "MelodyPipeline", "TransposePipeline"]
