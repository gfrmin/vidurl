"""
Optional LLM-powered fallback for video URL and listing-link extraction.

scrapegraphai's SmartScraperGraph runs a natural-language prompt over rendered
HTML. We invoke it on the post-Playwright DOM when both yt-dlp and the
heuristic extractor have failed.

The dependency is optional (extras group `llm`); we only import it lazily.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse

from .config import VideoExtractorConfig
from .exceptions import LLMNotConfiguredError, VideoExtractorError

logger = logging.getLogger(__name__)


PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
}


def _resolve_api_key(provider: str) -> Optional[str]:
    """Look up the API key in env first, then gnome-keyring via secret-tool."""
    env_var = PROVIDER_KEY_ENV.get(provider.lower())
    if env_var is None:
        return None
    value = os.environ.get(env_var)
    if value:
        return value
    if shutil.which("secret-tool"):
        try:
            result = subprocess.run(
                ["secret-tool", "lookup", "service", "env", "key", env_var],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except subprocess.SubprocessError as e:
            logger.debug(f"secret-tool lookup for {env_var} failed: {e}")
    return None


class LLMExtractor:
    """Wraps scrapegraphai's SmartScraperGraph for our two narrow questions."""

    def __init__(self, config: VideoExtractorConfig):
        if not config.enable_llm:
            raise LLMNotConfiguredError(
                "LLM tier is not configured (need --llm-provider and --llm-model)"
            )
        self.config = config
        self._graph_config = self._build_graph_config()

    def _build_graph_config(self) -> dict:
        provider = self.config.llm_provider.lower()
        model = self.config.llm_model
        api_key = _resolve_api_key(provider)
        if not api_key and provider != "ollama":
            env_var = PROVIDER_KEY_ENV.get(provider, "<unknown>")
            raise LLMNotConfiguredError(
                f"No API key for provider {provider!r}: set {env_var} in env or keyring"
            )

        # scrapegraphai expects "provider/model" naming
        llm_cfg = {"model": f"{provider}/{model}"}
        if api_key:
            llm_cfg["api_key"] = api_key
        return {"llm": llm_cfg, "verbose": self.config.verbose, "headless": True}

    def _run(self, prompt: str, source_html: str) -> dict:
        try:
            from scrapegraphai.graphs import SmartScraperGraph
        except ImportError as e:
            raise VideoExtractorError(
                "scrapegraphai is not installed; install with `pip install vidurl[llm]`"
            ) from e
        graph = SmartScraperGraph(prompt=prompt, source=source_html, config=self._graph_config)
        try:
            return graph.run() or {}
        except Exception as e:
            raise VideoExtractorError(f"LLM extraction failed: {e}") from e

    def find_video_url(self, html: str, base_url: str) -> Optional[str]:
        prompt = (
            "Inspect this rendered HTML for the URL of the main playable video file "
            "(e.g. .mp4, .webm, .m3u8, .mpd). Return JSON of shape "
            '{"video_url": "<absolute URL or null>"}. '
            "Only return a real video file URL, not a page URL."
        )
        result = self._run(prompt, html)
        url = result.get("video_url") if isinstance(result, dict) else None
        if not url or not isinstance(url, str):
            return None
        return urljoin(base_url, url)

    def find_video_links(self, html: str, base_url: str) -> list[str]:
        prompt = (
            "This page is a listing of videos. For each individual video, return the URL "
            "to that video's dedicated page. Output JSON of shape "
            '{"video_page_urls": ["<url>", ...]}. '
            "Only include URLs that lead to a single-video page, not category or tag pages."
        )
        result = self._run(prompt, html)
        raw = result.get("video_page_urls") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            return []
        self_url, _ = urldefrag(base_url)
        out: list[str] = []
        seen: set[str] = set()
        for href in raw:
            if not isinstance(href, str):
                continue
            abs_url, _ = urldefrag(urljoin(base_url, href))
            if not abs_url.startswith(("http://", "https://")):
                continue
            if abs_url == self_url or abs_url in seen:
                continue
            seen.add(abs_url)
            out.append(abs_url)
        return out


__all__ = ["LLMExtractor"]
