"""
Utility functions for the video extractor package.
"""

import time
import logging
import json
from typing import Dict, Any

from .config import VideoExtractorConfig
from .exceptions import VideoExtractorError


def setup_logging(config: VideoExtractorConfig) -> None:
    """Setup logging configuration based on verbosity settings."""
    if config.quiet:
        level = logging.WARNING
    elif config.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def retry_with_backoff(max_retries: int = 3, backoff_factor: float = 2.0, exceptions: tuple = (Exception,)):
    """Decorator to retry functions with exponential backoff."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    retries += 1
                    if retries >= max_retries:
                        raise
                    
                    wait_time = backoff_factor ** (retries - 1)
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Attempt {retries} failed: {e}. Retrying in {wait_time:.1f} seconds...")
                    time.sleep(wait_time)
            
            return func(*args, **kwargs)  # This should never be reached
        return wrapper
    return decorator


def load_config_from_file(config_path: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise VideoExtractorError(f"Failed to load configuration file: {e}")