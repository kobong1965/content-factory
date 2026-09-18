"""Local media processing worker for the content factory."""

__version__ = "0.1.0"

from .pipeline import MediaPipeline
from .queue import MediaTaskQueue
from .ocr import OcrEngineError, ocr_available, recognize_keyframes

__all__ = [
    "MediaPipeline", "MediaTaskQueue", "OcrEngineError", "ocr_available", "recognize_keyframes", "__version__"
]
