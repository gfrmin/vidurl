"""
Playwright-based video URL extraction.

Loads a page once, collects candidate video URLs from DOM, scripts, network
responses, post-click network activity, and embedded iframes, then validates
the candidates in parallel via curl Range requests.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .config import VideoExtractorConfig
from .exceptions import (
    BrowserSetupError,
    NetworkError,
    VideoExtractorError,
    VideoNotFoundError,
    VideoValidationError,
)

logger = logging.getLogger(__name__)


VIDEO_MIME_PREFIXES = ("video/", "application/vnd.apple.mpegurl", "application/dash+xml", "application/x-mpegurl")

VIDEO_REGEX_PATTERNS = [
    r"https?://[^\"'\s]+\.(?:mp4|webm|ogg|avi|mov|wmv|flv|m4v|mkv|ts|m2ts)(?:\?[^\"'\s]*)?",
    r"https?://[^\"'\s]+\.m3u8(?:\?[^\"'\s]*)?",
    r"https?://[^\"'\s]+\.mpd(?:\?[^\"'\s]*)?",
    r'"(?:video_url|videoUrl|src|source|file|hls|mp4)"\s*:\s*"([^"]+)"',
    r'file\s*:\s*["\']([^"\']+)["\']',
    r'source\s*:\s*["\']([^"\']+)["\']',
]

PLAY_SELECTORS = [
    'button[aria-label*="play" i]',
    'button[title*="play" i]',
    '.play-button',
    '.video-play-button',
    '[data-action="play"]',
    '[data-role="play"]',
    'button.play',
    '.play-btn',
    '.vjs-big-play-button',
    '.plyr__control--overlaid',
    '.jwplayer .jw-display-icon-display',
]

EMBED_PATTERNS = {
    "youtube": [r"youtube\.com/embed/", r"youtube\.com/watch\?", r"youtu\.be/"],
    "vimeo": [r"vimeo\.com/\d", r"player\.vimeo\.com/video/"],
    "dailymotion": [r"dailymotion\.com/(?:embed/)?video/"],
    "twitch": [r"twitch\.tv/videos/", r"clips\.twitch\.tv/"],
}


@dataclass
class CapturedResponse:
    url: str
    content_type: str
    status: int


@dataclass
class PageContext:
    """Holds the loaded page and observed network responses."""
    page: Page
    context: BrowserContext
    responses: list[CapturedResponse] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, response: Response) -> None:
        try:
            content_type = response.headers.get("content-type", "")
            with self._lock:
                self.responses.append(
                    CapturedResponse(
                        url=response.url,
                        content_type=content_type,
                        status=response.status,
                    )
                )
        except Exception:
            # Response object may already be gone; ignore.
            pass

    def cookies_for(self, url: str) -> list[dict]:
        return self.context.cookies(url)


class BrowserSession:
    """Context manager wrapping Playwright lifecycle for many pages."""

    def __init__(self, config: VideoExtractorConfig):
        self.config = config
        self._playwright = None
        self._browser: Optional[Browser] = None

    def __enter__(self) -> "BrowserSession":
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.config.headless)
        except Exception as e:
            self.close()
            raise BrowserSetupError(f"Failed to start Playwright Chromium: {e}") from e
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception as e:
                logger.debug(f"Error closing browser: {e}")
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as e:
                logger.debug(f"Error stopping playwright: {e}")
            self._playwright = None

    def load_page(self, url: str) -> PageContext:
        if self._browser is None:
            raise BrowserSetupError("BrowserSession not active")
        context = self._browser.new_context(
            user_agent=self.config.user_agent,
            viewport=self.config.viewport,
        )
        page = context.new_page()
        ctx = PageContext(page=page, context=context)
        page.on("response", ctx.record)

        try:
            page.goto(url, timeout=self.config.page_load_timeout * 1000, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            logger.warning(f"Page load timeout for {url}; continuing with partial content")

        try:
            page.wait_for_load_state("networkidle", timeout=self.config.network_idle_timeout * 1000)
        except PlaywrightTimeoutError:
            logger.debug("networkidle wait timed out; continuing")

        return ctx


def close_page_context(ctx: PageContext) -> None:
    try:
        ctx.page.close()
    except Exception:
        pass
    try:
        ctx.context.close()
    except Exception:
        pass


def find_candidate_video_urls(ctx: PageContext) -> set[str]:
    """Union of all candidate URLs from DOM, scripts, network, and iframes."""
    base_url = ctx.page.url
    try:
        html = ctx.page.content()
    except Exception as e:
        logger.warning(f"Failed to read page content: {e}")
        return set()
    soup = BeautifulSoup(html, "html.parser")

    candidates: set[str] = set()
    candidates |= _from_html5_video(soup, base_url)
    candidates |= _from_scripts(soup, base_url)
    candidates |= _from_network(ctx)
    candidates |= _from_iframes(soup, base_url)
    return candidates


def _from_html5_video(soup: BeautifulSoup, base_url: str) -> set[str]:
    urls: set[str] = set()
    for video in soup.find_all("video", src=True):
        urls.add(urljoin(base_url, video["src"]))
    for source in soup.find_all("source", src=True):
        urls.add(urljoin(base_url, source["src"]))
    return urls


def _from_scripts(soup: BeautifulSoup, base_url: str) -> set[str]:
    urls: set[str] = set()
    for script in soup.find_all("script"):
        if not script.string:
            continue
        for pattern in VIDEO_REGEX_PATTERNS:
            for match in re.findall(pattern, script.string, re.IGNORECASE):
                raw = match if isinstance(match, str) else (match[0] if match else "")
                if not raw:
                    continue
                urls.add(raw if raw.startswith("http") else urljoin(base_url, raw))
    return urls


def _from_network(ctx: PageContext) -> set[str]:
    urls: set[str] = set()
    with ctx._lock:
        responses = list(ctx.responses)
    for r in responses:
        if r.status not in (200, 206):
            continue
        ct = r.content_type.lower()
        if any(ct.startswith(prefix) for prefix in VIDEO_MIME_PREFIXES):
            urls.add(r.url)
            continue
        url_low = r.url.lower()
        if any(ext in url_low for ext in (".mp4", ".webm", ".m3u8", ".mpd", ".m4v", ".mkv", ".mov", ".ts")):
            urls.add(r.url)
    return urls


def _from_iframes(soup: BeautifulSoup, base_url: str) -> set[str]:
    urls: set[str] = set()
    for iframe in soup.find_all("iframe", src=True):
        full = urljoin(base_url, iframe["src"])
        for patterns in EMBED_PATTERNS.values():
            if any(re.search(p, full, re.IGNORECASE) for p in patterns):
                urls.add(full)
                break
    return urls


def trigger_lazy_load(ctx: PageContext) -> None:
    """Click visible play-button-shaped elements once and wait for new responses."""
    clicked = 0
    for selector in PLAY_SELECTORS:
        try:
            elements = ctx.page.query_selector_all(selector)
        except Exception:
            continue
        for el in elements[:2]:
            try:
                if not el.is_visible():
                    continue
                el.click(timeout=1500)
                clicked += 1
                if clicked >= 2:
                    break
            except Exception as e:
                logger.debug(f"Click on {selector} failed: {e}")
        if clicked >= 2:
            break

    if clicked:
        try:
            ctx.page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightTimeoutError:
            pass


def extract_video_for_page(ctx: PageContext, config: VideoExtractorConfig) -> Optional[str]:
    """Find candidate video URLs on a loaded page, validate, return curl command."""
    candidates = find_candidate_video_urls(ctx)
    if not candidates:
        trigger_lazy_load(ctx)
        candidates = find_candidate_video_urls(ctx)

    if not candidates:
        return None

    logger.info(f"Found {len(candidates)} candidate video URL(s); validating")
    referer = ctx.page.url
    cookies = ctx.cookies_for(referer)
    cookie_string = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    return _validate_first(list(candidates), referer, cookie_string, config)


def _validate_first(
    candidates: list[str],
    referer: str,
    cookie_string: str,
    config: VideoExtractorConfig,
) -> Optional[str]:
    """Validate URLs in parallel, return curl command for the first that passes."""
    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        futures = {
            pool.submit(_validate_one, url, referer, cookie_string, config): url
            for url in candidates
        }
        for future in as_completed(futures):
            try:
                command = future.result()
                if command:
                    logger.info(f"Validated: {futures[future]}")
                    return command
            except Exception as e:
                logger.debug(f"Validation failed for {futures[future]}: {e}")
    return None


def _validate_one(
    video_url: str,
    referer: str,
    cookie_string: str,
    config: VideoExtractorConfig,
) -> Optional[str]:
    """Probe with Range request; if successful, return the full curl download command."""
    test_cmd = [
        "curl", "-L", "-s",
        "--max-time", str(config.curl_timeout),
        "-H", f"Range: bytes=0-{config.validation_chunk_size - 1}",
        "-H", f"User-Agent: {config.user_agent}",
        "-H", f"Referer: {referer}",
        "-H", "Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,*/*;q=0.5",
        "-H", "Accept-Language: en-US,en;q=0.9",
        "-H", "Connection: keep-alive",
    ]
    if cookie_string:
        test_cmd += ["-H", f"Cookie: {cookie_string}"]
    test_cmd += ["-w", "%{http_code},%{size_download}", "-o", "/dev/null", video_url]

    try:
        result = subprocess.run(
            test_cmd, capture_output=True, text=True, timeout=config.curl_timeout + 5
        )
    except subprocess.TimeoutExpired as e:
        raise NetworkError(f"curl validation timed out for {video_url}") from e
    except subprocess.SubprocessError as e:
        raise NetworkError(f"curl invocation failed: {e}") from e

    if result.returncode != 0:
        raise NetworkError(f"curl exited {result.returncode} for {video_url}")

    parts = result.stdout.strip().split(",")
    if len(parts) != 2:
        raise VideoValidationError(f"unexpected curl output: {result.stdout!r}")
    status, size_str = parts
    size = int(size_str) if size_str.isdigit() else 0
    if status not in ("200", "206"):
        raise VideoValidationError(f"HTTP {status} for {video_url}")
    if size < config.min_download_size:
        raise VideoValidationError(f"only {size} bytes for {video_url}")

    return build_curl_command(video_url, referer, cookie_string, config)


def build_curl_command(
    video_url: str,
    referer: str,
    cookie_string: str,
    config: VideoExtractorConfig,
) -> str:
    """Compose the final curl download command for a validated video URL."""
    filename = os.path.basename(urlparse(video_url).path)
    if not filename or "." not in filename:
        filename = config.default_filename
    if config.output_dir and config.output_dir != ".":
        filename = os.path.join(config.output_dir, filename)

    headers = [
        f"User-Agent: {config.user_agent}",
        f"Referer: {referer}",
        "Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,*/*;q=0.5",
        "Accept-Language: en-US,en;q=0.9",
        "Accept-Encoding: gzip, deflate, br",
        "Connection: keep-alive",
        "Sec-Fetch-Dest: video",
        "Sec-Fetch-Mode: no-cors",
        "Sec-Fetch-Site: same-origin",
    ]
    parts: list[str] = ["curl", "-L", "--progress-bar", "-o", filename]
    for h in headers:
        parts += ["-H", h]
    if cookie_string:
        parts += ["-H", f"Cookie: {cookie_string}"]
    parts.append(video_url)

    return " ".join(shlex.quote(p) for p in parts)


def extract_video(url: str, config: VideoExtractorConfig) -> str:
    """Convenience: one-shot load page + extract video. Raises if nothing found."""
    with BrowserSession(config) as session:
        ctx = session.load_page(url)
        try:
            command = extract_video_for_page(ctx, config)
        finally:
            close_page_context(ctx)
    if not command:
        raise VideoNotFoundError(f"no video URL found on {url}")
    return command


__all__ = [
    "BrowserSession",
    "PageContext",
    "build_curl_command",
    "close_page_context",
    "extract_video",
    "extract_video_for_page",
    "find_candidate_video_urls",
    "trigger_lazy_load",
]
