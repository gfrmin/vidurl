"""
Video URL Extractor

A Python package for extracting downloadable video URLs from web pages
and generating validated curl commands for downloading.
"""

from .extractor import VideoExtractor
from .config import VideoExtractorConfig
from .exceptions import (
    VideoExtractorError,
    WebDriverSetupError,
    VideoNotFoundError,
    VideoValidationError,
    NetworkError
)

__version__ = "0.1.0"
__author__ = "vidurl"
__description__ = "Extract video URLs from web pages and generate curl download commands"

__all__ = [
    'VideoExtractor',
    'VideoExtractorConfig',
    'VideoExtractorError',
    'WebDriverSetupError',
    'VideoNotFoundError', 
    'VideoValidationError',
    'NetworkError',
]