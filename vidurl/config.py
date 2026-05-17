"""
Configuration class for the video extractor.
"""

from dataclasses import dataclass, field
from typing import List, Optional


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class VideoExtractorConfig:
    """Configuration for the vidurl pipeline."""

    # Timeouts (seconds)
    page_load_timeout: int = 15
    curl_timeout: int = 15
    video_detection_timeout: int = 30
    network_idle_timeout: int = 5

    # Browser
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_agent: str = DEFAULT_USER_AGENT
    headless: bool = True

    # Video URL classification
    video_extensions: List[str] = field(default_factory=lambda: [
        ".mp4", ".webm", ".ogg", ".avi", ".mov", ".wmv",
        ".flv", ".m4v", ".mkv", ".ts", ".m2ts",
    ])
    streaming_segments: List[str] = field(default_factory=lambda: [
        ".m3u8", ".mpd", "/segment", "/chunk", "/playlist", "/manifest",
    ])
    min_download_size: int = 1024
    validation_chunk_size: int = 1048576

    # Output
    output_dir: str = "."
    default_filename: str = "video.mp4"
    verbose: bool = False
    quiet: bool = False

    # Retries / concurrency
    max_retries: int = 3
    retry_backoff_factor: float = 2.0
    max_workers: int = 4

    # yt-dlp
    enable_ytdlp: bool = True
    ytdlp_extra_args: List[str] = field(default_factory=list)

    # Listing mode
    force_listing: bool = False
    disable_listing: bool = False
    link_selector: Optional[str] = None
    link_pattern: Optional[str] = None
    listing_min_links: int = 3

    # LLM tier
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    disable_llm: bool = False

    @property
    def enable_llm(self) -> bool:
        return (
            not self.disable_llm
            and bool(self.llm_provider)
            and bool(self.llm_model)
        )

    @property
    def viewport(self) -> dict:
        return {"width": self.viewport_width, "height": self.viewport_height}
