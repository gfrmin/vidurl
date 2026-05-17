"""
vidurl — yt-dlp-first video extractor with Playwright + optional LLM fallback.
"""

from .config import VideoExtractorConfig
from .exceptions import (
    BrowserSetupError,
    ListingNotFoundError,
    LLMNotConfiguredError,
    NetworkError,
    VideoExtractorError,
    VideoNotFoundError,
    VideoValidationError,
)
from .pipeline import Pipeline, PipelineResult

__version__ = "0.2.0"
__description__ = "yt-dlp-first video extractor with Playwright + optional LLM fallback"

__all__ = [
    "Pipeline",
    "PipelineResult",
    "VideoExtractorConfig",
    "VideoExtractorError",
    "BrowserSetupError",
    "VideoNotFoundError",
    "VideoValidationError",
    "NetworkError",
    "ListingNotFoundError",
    "LLMNotConfiguredError",
]
