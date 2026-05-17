"""
Per-URL extraction pipeline: yt-dlp → Playwright (video / listing) → LLM fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import VideoExtractorConfig
from .downloader import curl_download, ytdlp_can_handle, ytdlp_download
from .exceptions import (
    BrowserSetupError,
    LLMNotConfiguredError,
    VideoExtractorError,
)
from .extractor import (
    BrowserSession,
    PageContext,
    build_curl_command,
    close_page_context,
    extract_video_for_page,
)
from .listing import extract_video_links

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    successes: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    def record_success(self, url: str) -> None:
        self.successes.append(url)

    def record_failure(self, url: str, reason: str) -> None:
        self.failures.append((url, reason))


class Pipeline:
    def __init__(self, config: VideoExtractorConfig, dry_run: bool):
        self.config = config
        self.dry_run = dry_run
        self._session: Optional[BrowserSession] = None
        self._llm = None  # lazily constructed
        self.result = PipelineResult()

    def __enter__(self) -> "Pipeline":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._close_session()
        return False

    def _ensure_session(self) -> BrowserSession:
        if self._session is None:
            self._session = BrowserSession(self.config).__enter__()
        return self._session

    def _close_session(self) -> None:
        if self._session is not None:
            try:
                self._session.__exit__(None, None, None)
            finally:
                self._session = None

    def _ensure_llm(self):
        if self._llm is not None:
            return self._llm
        if not self.config.enable_llm:
            return None
        try:
            from .llm import LLMExtractor
            self._llm = LLMExtractor(self.config)
        except LLMNotConfiguredError as e:
            logger.warning(f"LLM tier requested but unavailable: {e}")
            self._llm = None
        return self._llm

    def process(self, url: str, depth: int = 0) -> bool:
        """Process one URL through the escalation ladder. Returns True on success."""
        indent = "  " * depth
        logger.info(f"{indent}Processing: {url}")

        if self.config.enable_ytdlp and ytdlp_can_handle(url):
            logger.info(f"{indent}yt-dlp can handle this URL")
            try:
                ytdlp_download(url, self.config, self.dry_run)
                self.result.record_success(url)
                return True
            except VideoExtractorError as e:
                logger.warning(f"{indent}yt-dlp failed: {e}")

        # Tiers 2/3 share a loaded page.
        try:
            session = self._ensure_session()
        except BrowserSetupError as e:
            logger.error(f"{indent}Browser unavailable: {e}")
            self.result.record_failure(url, f"browser setup failed: {e}")
            return False

        ctx = session.load_page(url)
        try:
            return self._process_loaded(url, ctx, depth)
        finally:
            close_page_context(ctx)

    def _process_loaded(self, url: str, ctx: PageContext, depth: int) -> bool:
        indent = "  " * depth
        cfg = self.config

        if not cfg.force_listing:
            command = extract_video_for_page(ctx, cfg)
            if command:
                logger.info(f"{indent}Playwright found video")
                curl_download(command, self.dry_run)
                self.result.record_success(url)
                return True

        if cfg.disable_listing:
            self.result.record_failure(url, "no video; listing disabled")
            return self._try_llm_video(url, ctx, depth)

        links = extract_video_links(
            ctx.page,
            ctx.page.url,
            selector=cfg.link_selector,
            pattern=cfg.link_pattern,
            min_links=cfg.listing_min_links,
        )
        if links:
            logger.info(f"{indent}Found {len(links)} candidate video-page link(s)")
            return self._recurse_links(links, depth)

        if self._try_llm_listing(url, ctx, depth):
            return True
        if self._try_llm_video(url, ctx, depth):
            return True

        logger.warning(f"{indent}No video and no listing links on {url}")
        self.result.record_failure(url, "no video, no listing links")
        return False

    def _recurse_links(self, links: list[str], depth: int) -> bool:
        any_success = False
        for link in links:
            try:
                if self.process(link, depth + 1):
                    any_success = True
            except Exception as e:
                logger.warning(f"  Failed on {link}: {e}")
                self.result.record_failure(link, str(e))
        return any_success

    def _try_llm_video(self, url: str, ctx: PageContext, depth: int) -> bool:
        llm = self._ensure_llm()
        if llm is None:
            return False
        indent = "  " * depth
        try:
            html = ctx.page.content()
            video_url = llm.find_video_url(html, ctx.page.url)
            if not video_url:
                return False
            logger.info(f"{indent}LLM proposed video URL: {video_url}")
            referer = ctx.page.url
            cookies = ctx.cookies_for(referer)
            cookie_string = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            from .extractor import _validate_one
            command = _validate_one(video_url, referer, cookie_string, self.config)
            if not command:
                logger.warning(f"{indent}LLM video URL failed validation")
                return False
            curl_download(command, self.dry_run)
            self.result.record_success(url)
            return True
        except VideoExtractorError as e:
            logger.warning(f"{indent}LLM video extraction failed: {e}")
            return False

    def _try_llm_listing(self, url: str, ctx: PageContext, depth: int) -> bool:
        llm = self._ensure_llm()
        if llm is None:
            return False
        indent = "  " * depth
        try:
            html = ctx.page.content()
            links = llm.find_video_links(html, ctx.page.url)
            if not links:
                return False
            logger.info(f"{indent}LLM proposed {len(links)} video-page link(s)")
            return self._recurse_links(links, depth)
        except VideoExtractorError as e:
            logger.warning(f"{indent}LLM listing extraction failed: {e}")
            return False


__all__ = ["Pipeline", "PipelineResult"]
