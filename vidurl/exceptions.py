"""
Exception classes for the video extractor package.
"""


class VideoExtractorError(Exception):
    """Base exception for video extractor errors."""
    pass


class BrowserSetupError(VideoExtractorError):
    """Raised when Playwright browser setup fails."""
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


class ListingNotFoundError(VideoExtractorError):
    """Raised when listing-link discovery yields no candidates."""
    pass


class LLMNotConfiguredError(VideoExtractorError):
    """Raised when the LLM tier is reached but provider/model are not configured."""
    pass
