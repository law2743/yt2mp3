"""Optional model backends kept outside the lightweight FastAPI environment."""

from app.services.model_backends.demucs_backend import DemucsStemSeparator

__all__ = ["DemucsStemSeparator"]
