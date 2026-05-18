"""
Optional LLM-powered fallback for video URL and listing-link extraction.

scrapegraphai's SmartScraperGraph runs a natural-language prompt over rendered
HTML. We invoke it on the post-Playwright DOM when both yt-dlp and the
heuristic extractor have failed.

When the primary model is local (Ollama) and refuses to comply with the
extraction prompt, we transparently retry against an abliterated/uncensored
model named via `llm_fallback_model`. Refusal is detected by running a
second LLM classification pass over the raw model output.

The dependency is optional (extras group `llm`); we only import it lazily.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Optional
from urllib.parse import urldefrag, urljoin

from .config import VideoExtractorConfig
from .exceptions import LLMNotConfiguredError, VideoExtractorError

logger = logging.getLogger(__name__)


PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
}


OLLAMA_URL = "http://localhost:11434/api/generate"
RAW_PROBE_HTML_CHARS = 12000
REFUSAL_CLASSIFIER_PROMPT = (
    "The text below is an AI assistant's reply to a request to extract URLs "
    "from HTML. Did the assistant refuse to perform the task (e.g. citing "
    "safety, policy, ethics, or inability) rather than attempting it?\n"
    "Reply with exactly one word: REFUSED or COMPLIED.\n\n"
    "Text:\n{text}"
)


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
        self._provider = config.llm_provider.lower()
        self._primary_model = config.llm_model
        self._fallback_model = config.llm_fallback_model
        self._api_key = self._resolve_provider_key()

    def _resolve_provider_key(self) -> Optional[str]:
        api_key = _resolve_api_key(self._provider)
        if not api_key and self._provider != "ollama":
            env_var = PROVIDER_KEY_ENV.get(self._provider, "<unknown>")
            raise LLMNotConfiguredError(
                f"No API key for provider {self._provider!r}: "
                f"set {env_var} in env or keyring"
            )
        return api_key

    def _build_graph_config(self, model: str) -> dict:
        llm_cfg: dict = {"model": f"{self._provider}/{model}"}
        if self._api_key:
            llm_cfg["api_key"] = self._api_key

        graph_cfg: dict = {
            "llm": llm_cfg,
            "verbose": self.config.verbose,
            "headless": True,
        }
        # Ollama: scrapegraphai's default embedding model is OpenAI's; if the user
        # picked Ollama for the LLM they almost certainly don't want an OpenAI key
        # requirement smuggled in via embeddings.
        if self._provider == "ollama":
            graph_cfg["embeddings"] = {"model": "ollama/nomic-embed-text"}
        return graph_cfg

    def _run_with_model(self, prompt: str, source_html: str, model: str) -> dict:
        try:
            from scrapegraphai.graphs import SmartScraperGraph
        except ImportError as e:
            raise VideoExtractorError(
                "scrapegraphai is not installed; install with `pip install vidurl[llm]`"
            ) from e
        graph = SmartScraperGraph(
            prompt=prompt, source=source_html, config=self._build_graph_config(model)
        )
        try:
            return graph.run() or {}
        except Exception as e:
            raise VideoExtractorError(f"LLM extraction failed: {e}") from e

    def _raw_ollama_call(self, model: str, prompt: str) -> Optional[str]:
        """Direct generate call against local Ollama; returns raw text or None."""
        try:
            import requests
        except ImportError:
            logger.debug("requests not available; skipping refusal probe")
            return None
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response")
        except Exception as e:
            logger.debug(f"Raw Ollama probe failed: {e}")
            return None

    def _is_refusal(self, raw_text: str) -> bool:
        """Ask the primary model whether the given text was a refusal."""
        if not raw_text or not raw_text.strip():
            return False
        classifier = REFUSAL_CLASSIFIER_PROMPT.format(text=raw_text[:4000])
        verdict = self._raw_ollama_call(self._primary_model, classifier)
        if not verdict:
            return False
        token = verdict.strip().split()[0].upper().strip(".,!?:")
        is_refused = token == "REFUSED"
        logger.debug(f"Refusal classifier verdict: {token!r} → refused={is_refused}")
        return is_refused

    def _should_probe_refusal(self) -> bool:
        return self._provider == "ollama" and bool(self._fallback_model)

    def _run(self, prompt: str, source_html: str) -> dict:
        result = self._run_with_model(prompt, source_html, self._primary_model)
        if result or not self._should_probe_refusal():
            return result

        probe_prompt = f"{prompt}\n\nHTML:\n{source_html[:RAW_PROBE_HTML_CHARS]}"
        raw = self._raw_ollama_call(self._primary_model, probe_prompt)
        if raw is None or not self._is_refusal(raw):
            return result

        logger.info(
            f"LLM ({self._primary_model}) refused; "
            f"retrying with fallback model {self._fallback_model}"
        )
        return self._run_with_model(prompt, source_html, self._fallback_model)

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

    def find_next_page_url(self, html: str, base_url: str) -> Optional[str]:
        prompt = (
            "This page is one page of a paginated listing of videos. Identify the URL "
            "of the NEXT page in the pagination (not the previous, not page 1, not a "
            "video page). Output JSON of shape "
            '{"next_page_url": "<absolute URL or null>"}. '
            "Return null if there is no next page."
        )
        result = self._run(prompt, html)
        url = result.get("next_page_url") if isinstance(result, dict) else None
        if not url or not isinstance(url, str):
            return None
        abs_url, _ = urldefrag(urljoin(base_url, url))
        if not abs_url.startswith(("http://", "https://")):
            return None
        self_url, _ = urldefrag(base_url)
        if abs_url == self_url:
            return None
        return abs_url

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
