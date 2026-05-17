"""
Per-URL extraction pipeline: yt-dlp → Playwright (video / listing) → LLM fallback.

Listing-page handling automatically paginates: after processing one listing
page, vidurl finds the next page (rel=next, anchor heuristics, URL-template
inference, or LLM fallback) and continues, capped by `config.max_pages`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urldefrag

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
    close_page_context,
    extract_video_for_page,
)
from .listing import (
    Shape,
    _dominant_shape,
    extract_video_links,
    find_next_page,
)

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
        self.visited_listing_urls: set[str] = set()

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

        try:
            session = self._ensure_session()
        except BrowserSetupError as e:
            logger.error(f"{indent}Browser unavailable: {e}")
            self.result.record_failure(url, f"browser setup failed: {e}")
            return False

        ctx = session.load_page(url)
        try:
            return self._process_loaded(url, ctx, depth, is_first_page=True)
        finally:
            close_page_context(ctx)

    def _process_loaded(
        self,
        url: str,
        ctx: PageContext,
        depth: int,
        *,
        is_first_page: bool,
    ) -> bool:
        indent = "  " * depth
        cfg = self.config

        # Only attempt in-page video extraction on the first listing page; subsequent
        # pages in a pagination walk are known to be listings.
        if is_first_page and not cfg.force_listing:
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
            any_success = self._recurse_links(links, depth)
            if cfg.enable_pagination and is_first_page:
                self._continue_pagination(ctx, links, depth)
            return any_success

        if self._try_llm_listing(url, ctx, depth):
            if cfg.enable_pagination and is_first_page:
                # If LLM discovered links, also try LLM-driven pagination.
                self._continue_pagination(ctx, [], depth, force_llm_next=True)
            return True
        if self._try_llm_video(url, ctx, depth):
            return True

        logger.warning(f"{indent}No video and no listing links on {url}")
        self.result.record_failure(url, "no video, no listing links")
        return False

    def _continue_pagination(
        self,
        first_ctx: PageContext,
        first_page_links: list[str],
        depth: int,
        *,
        force_llm_next: bool = False,
    ) -> None:
        """After the first listing page has been processed, walk to subsequent pages.

        The caller owns `first_ctx` and is responsible for closing it. Every
        intermediate paginated context is owned by this method and closed here.
        """
        cfg = self.config
        indent = "  " * depth
        video_shape: Optional[Shape] = _dominant_shape(first_page_links, cfg.listing_min_links)

        start_url, _ = urldefrag(first_ctx.page.url)
        self.visited_listing_urls.add(start_url)

        current_ctx = first_ctx
        owns_current = False  # caller owns first_ctx
        page_number = 2
        try:
            while page_number <= cfg.max_pages:
                next_url = self._discover_next(
                    current_ctx,
                    page_number=page_number,
                    video_shape=video_shape,
                    force_llm=force_llm_next,
                )
                if not next_url:
                    logger.info(f"{indent}No next listing page; stopping pagination")
                    return
                next_norm, _ = urldefrag(next_url)
                if next_norm in self.visited_listing_urls:
                    logger.info(f"{indent}Already visited {next_norm}; stopping pagination")
                    return
                self.visited_listing_urls.add(next_norm)

                logger.info(f"{indent}Pagination → page {page_number}: {next_norm}")
                session = self._ensure_session()
                next_ctx = session.load_page(next_norm)

                if owns_current:
                    close_page_context(current_ctx)
                current_ctx = next_ctx
                owns_current = True

                links = extract_video_links(
                    current_ctx.page,
                    current_ctx.page.url,
                    selector=cfg.link_selector,
                    pattern=cfg.link_pattern,
                    min_links=cfg.listing_min_links,
                )
                if not links:
                    links = self._llm_listing_links(current_ctx)
                if links:
                    if video_shape is None:
                        video_shape = _dominant_shape(links, cfg.listing_min_links)
                    self._recurse_links(links, depth)
                else:
                    logger.warning(f"{indent}No links on paginated page {next_norm}")
                page_number += 1
            logger.info(f"{indent}Reached --max-pages={cfg.max_pages}; stopping pagination")
        finally:
            if owns_current:
                close_page_context(current_ctx)

    def _discover_next(
        self,
        ctx: PageContext,
        *,
        page_number: int,
        video_shape: Optional[Shape],
        force_llm: bool,
    ) -> Optional[str]:
        cfg = self.config
        if not force_llm:
            candidate = find_next_page(
                ctx.page,
                ctx.page.url,
                selector=cfg.next_selector,
                pattern=cfg.next_pattern,
                template=cfg.page_url_template,
                next_page_number=page_number,
                video_link_shape=video_shape,
            )
            if candidate:
                return candidate
        # Heuristics gave up; try the LLM.
        return self._llm_next_page(ctx)

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
            links = self._llm_listing_links(ctx)
            if not links:
                return False
            logger.info(f"{indent}LLM proposed {len(links)} video-page link(s)")
            return self._recurse_links(links, depth)
        except VideoExtractorError as e:
            logger.warning(f"{indent}LLM listing extraction failed: {e}")
            return False

    def _llm_listing_links(self, ctx: PageContext) -> list[str]:
        llm = self._ensure_llm()
        if llm is None:
            return []
        try:
            html = ctx.page.content()
            return llm.find_video_links(html, ctx.page.url)
        except VideoExtractorError as e:
            logger.warning(f"LLM listing extraction failed: {e}")
            return []

    def _llm_next_page(self, ctx: PageContext) -> Optional[str]:
        llm = self._ensure_llm()
        if llm is None:
            return None
        try:
            html = ctx.page.content()
            return llm.find_next_page_url(html, ctx.page.url)
        except VideoExtractorError as e:
            logger.warning(f"LLM next-page lookup failed: {e}")
            return None


__all__ = ["Pipeline", "PipelineResult"]
