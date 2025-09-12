"""
Exception classes for the video extractor package.
"""


class VideoExtractorError(Exception):
    """Base exception for video extractor errors."""
    pass


class WebDriverSetupError(VideoExtractorError):
    """Raised when WebDriver setup fails."""
    pass


class VideoNotFoundError(VideoExtractorError):
    """Raised when no video URLs are found."""
    pass


class VideoValidationError(VideoExtractorError):
    """Raised when video URL validation fails."""
    pass


class NetworkError(VideoExtractorError):
    """Raised when network operations fail."""
    pass