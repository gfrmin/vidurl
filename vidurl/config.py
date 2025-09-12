"""
Configuration class for the video extractor.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class VideoExtractorConfig:
    """Configuration class for video extractor settings."""
    # Timeout settings (in seconds)
    page_load_timeout: int = 10
    curl_timeout: int = 10
    video_detection_timeout: int = 30
    
    # Browser settings
    window_size: str = "1920,1080"
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    headless: bool = True
    
    # Video detection settings
    video_extensions: List[str] = None
    streaming_segments: List[str] = None
    min_download_size: int = 1024  # bytes
    validation_chunk_size: int = 1048576  # 1MB
    
    # Output settings
    output_dir: str = "."
    default_filename: str = "video.mp4"
    verbose: bool = False
    quiet: bool = False
    
    # Retry settings
    max_retries: int = 3
    retry_backoff_factor: float = 2.0
    
    # Parallel processing
    max_workers: int = 4
    
    def __post_init__(self):
        if self.video_extensions is None:
            self.video_extensions = ['.mp4', '.webm', '.ogg', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.mkv']
        
        if self.streaming_segments is None:
            self.streaming_segments = ['.m3u8', '.mpd', '/segment', '/chunk', '/playlist']