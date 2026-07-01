"""Application pipelines composed from replaceable service adapters."""

from app.services.pipelines.analyze import AnalyzePipeline
from app.services.pipelines.melody_fusion_pipeline import MelodyFusionPipeline
from app.services.pipelines.stems import StemPipeline
from app.services.pipelines.transpose import TransposePipeline

__all__ = ["AnalyzePipeline", "MelodyFusionPipeline", "StemPipeline", "TransposePipeline"]
