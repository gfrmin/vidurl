"""
Download primitives: yt-dlp probe/invoke and curl command execution.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from typing import Sequence

from .config import VideoExtractorConfig
from .exceptions import NetworkError, VideoExtractorError

logger = logging.getLogger(__name__)


def _ytdlp_binary() -> str:
    binary = shutil.which("yt-dlp")
    if not binary:
        raise VideoExtractorError("yt-dlp executable not found in PATH")
    return binary


def ytdlp_can_handle(url: str, timeout: int = 30) -> bool:
    """Return True if yt-dlp has an extractor that matches the URL.

    Uses `yt-dlp --simulate -q -j <url>` which exits non-zero when no extractor
    matches or when the page fails entirely. We treat any zero exit as success.
    """
    try:
        binary = _ytdlp_binary()
    except VideoExtractorError:
        return False

    try:
        result = subprocess.run(
            [binary, "--simulate", "-q", "--no-warnings", "-j", url],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.debug(f"yt-dlp probe timed out for {url}")
        return False
    except subprocess.SubprocessError as e:
        logger.debug(f"yt-dlp probe failed: {e}")
        return False

    return result.returncode == 0


def _ytdlp_command(url: str, config: VideoExtractorConfig) -> list[str]:
    binary = _ytdlp_binary()
    output_template = os.path.join(config.output_dir, "%(title)s.%(ext)s")
    cmd = [binary, "-o", output_template]
    if config.ytdlp_extra_args:
        cmd.extend(config.ytdlp_extra_args)
    cmd.append(url)
    return cmd


def ytdlp_download(url: str, config: VideoExtractorConfig, dry_run: bool) -> str:
    """Run yt-dlp (or print the command for dry-run). Returns the command string."""
    cmd = _ytdlp_command(url, config)
    cmd_str = " ".join(shlex.quote(p) for p in cmd)

    if dry_run:
        logger.info(f"[dry-run] yt-dlp command: {cmd_str}")
        print(cmd_str)
        return cmd_str

    logger.info(f"Running yt-dlp: {cmd_str}")
    try:
        result = subprocess.run(cmd)
    except subprocess.SubprocessError as e:
        raise NetworkError(f"yt-dlp execution failed: {e}") from e
    if result.returncode != 0:
        raise NetworkError(f"yt-dlp exited {result.returncode}")
    return cmd_str


def curl_download(curl_command: str, dry_run: bool) -> str:
    """Run a curl command produced by the extractor (or print it for dry-run)."""
    if dry_run:
        logger.info(f"[dry-run] curl command: {curl_command}")
        print(curl_command)
        return curl_command

    logger.info("Running curl download")
    args = shlex.split(curl_command)
    try:
        result = subprocess.run(args)
    except subprocess.SubprocessError as e:
        raise NetworkError(f"curl execution failed: {e}") from e
    if result.returncode != 0:
        raise NetworkError(f"curl exited {result.returncode}")
    return curl_command


__all__ = [
    "ytdlp_can_handle",
    "ytdlp_download",
    "curl_download",
]
