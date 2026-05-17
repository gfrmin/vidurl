"""
Listing-page detection, link extraction, and next-page discovery.

Given a Playwright page, find candidate links that look like individual video
pages — either via explicit selector/pattern, or auto-detect by grouping links
by URL "shape" (path prefix + query keys) and taking the largest group.

Next-page discovery follows the same "union of strategies, first viable wins"
shape used for video discovery: rel=next link, anchor heuristics, URL-template
inference.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from playwright.sync_api import Page

logger = logging.getLogger(__name__)


Shape = tuple[str, str, tuple[str, ...]]

NEXT_TEXT_RE = re.compile(
    r"^\s*(?:next(?:\s+page)?|»|›|→|more|older(?:\s+posts?)?)\s*$",
    re.IGNORECASE,
)

# Patterns we know how to increment. The named group "n" captures the page number.
PAGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?P<prefix>[?&](?:page|p|pg|paged)=)(?P<n>\d+)"),
    re.compile(r"(?P<prefix>[?&](?:offset|start)=)(?P<n>\d+)"),
    re.compile(r"(?P<prefix>/page/)(?P<n>\d+)(?P<suffix>/?)$"),
    re.compile(r"(?P<prefix>/(?:p|pg)/)(?P<n>\d+)(?P<suffix>/?)$"),
    re.compile(r"(?P<prefix>[/-])(?P<n>\d+)(?P<suffix>/?)$"),
]


def _all_anchor_records(page: Page, base_url: str) -> list[dict]:
    """Return [{href, text, aria, rel}] for every <a> on the page."""
    try:
        records = page.eval_on_selector_all(
            "a[href]",
            """els => els.map(e => ({
                href: e.getAttribute('href'),
                text: (e.innerText || '').trim(),
                aria: e.getAttribute('aria-label') || '',
                rel: e.getAttribute('rel') || '',
            }))""",
        )
    except Exception as e:
        logger.debug(f"Failed to collect anchors: {e}")
        return []
    out: list[dict] = []
    for r in records:
        if not r or not r.get("href"):
            continue
        abs_url, _ = urldefrag(urljoin(base_url, r["href"]))
        if not abs_url.startswith(("http://", "https://")):
            continue
        out.append({"href": abs_url, "text": r.get("text") or "", "aria": r.get("aria") or "", "rel": r.get("rel") or ""})
    return out


def _all_anchor_hrefs(page: Page, base_url: str) -> list[str]:
    return [r["href"] for r in _all_anchor_records(page, base_url)]


def _url_shape(url: str) -> Shape:
    """Group key: (netloc, path-template-without-last-segment, sorted query keys)."""
    p = urlparse(url)
    path_parts = [seg for seg in p.path.split("/") if seg]
    template = "/" + "/".join(path_parts[:-1]) if len(path_parts) > 1 else "/"
    query_keys = tuple(sorted({kv.split("=")[0] for kv in p.query.split("&") if kv}))
    return (p.netloc, template, query_keys)


def _dominant_shape(hrefs: list[str], min_count: int) -> Optional[Shape]:
    if not hrefs:
        return None
    counter = Counter(_url_shape(h) for h in hrefs)
    shape, count = counter.most_common(1)[0]
    return shape if count >= min_count else None


def extract_video_links(
    page: Page,
    base_url: str,
    selector: Optional[str] = None,
    pattern: Optional[str] = None,
    min_links: int = 3,
) -> list[str]:
    """Extract candidate video-page links from the loaded page."""
    self_url, _ = urldefrag(base_url)

    if selector:
        try:
            hrefs = page.eval_on_selector_all(
                selector, "els => els.map(e => e.getAttribute('href'))"
            )
        except Exception as e:
            logger.warning(f"Selector {selector!r} failed: {e}")
            hrefs = []
        candidates: list[str] = []
        for href in hrefs:
            if not href:
                continue
            abs_url, _ = urldefrag(urljoin(base_url, href))
            if abs_url != self_url and abs_url.startswith(("http://", "https://")):
                candidates.append(abs_url)
        return _dedupe(candidates)

    all_hrefs = _all_anchor_hrefs(page, base_url)
    all_hrefs = [h for h in all_hrefs if h != self_url]

    if pattern:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            logger.warning(f"Invalid --link-pattern {pattern!r}: {e}")
            return []
        return _dedupe(h for h in all_hrefs if regex.search(h))

    if not all_hrefs:
        return []

    shape_counts: Counter[Shape] = Counter()
    by_shape: dict[Shape, list[str]] = {}
    for h in all_hrefs:
        shape = _url_shape(h)
        shape_counts[shape] += 1
        by_shape.setdefault(shape, []).append(h)

    best_shape, best_count = shape_counts.most_common(1)[0]
    if best_count < min_links:
        return []
    return _dedupe(by_shape[best_shape])


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def looks_like_listing(page: Page, base_url: str, min_links: int = 3) -> bool:
    return len(extract_video_links(page, base_url, min_links=min_links)) >= min_links


def _increment_page_in_url(url: str) -> Optional[str]:
    """Find a numeric page indicator in `url` and return the +1 URL, else None."""
    for pat in PAGE_PATTERNS:
        m = pat.search(url)
        if not m:
            continue
        try:
            n = int(m.group("n"))
        except (ValueError, IndexError):
            continue
        replacement = url[: m.start("n")] + str(n + 1) + url[m.end("n") :]
        return replacement
    return None


def _probe_url(url: str, timeout: float = 8.0) -> bool:
    """HEAD then GET probe; True if any returns < 400."""
    for method in ("head", "get"):
        try:
            resp = getattr(requests, method)(
                url, allow_redirects=True, timeout=timeout,
                stream=method == "get",
                headers={"User-Agent": "vidurl/1.0"},
            )
            if method == "get":
                resp.close()
            if resp.status_code < 400:
                return True
        except requests.RequestException as e:
            logger.debug(f"Probe {method} {url} failed: {e}")
    return False


def _from_rel_next(page: Page, base_url: str) -> Optional[str]:
    for selector in ('link[rel~="next"]', 'a[rel~="next"]'):
        try:
            handle = page.query_selector(selector)
        except Exception:
            continue
        if not handle:
            continue
        href = handle.get_attribute("href")
        if not href:
            continue
        abs_url, _ = urldefrag(urljoin(base_url, href))
        if abs_url.startswith(("http://", "https://")):
            return abs_url
    return None


def _from_anchor_text(
    records: list[dict],
    base_url: str,
    video_shape: Optional[Shape],
) -> Optional[str]:
    self_url, _ = urldefrag(base_url)
    for r in records:
        text = r["text"]
        aria = r["aria"]
        rel = r["rel"]
        href = r["href"]
        if href == self_url:
            continue
        matches = (
            NEXT_TEXT_RE.match(text)
            or NEXT_TEXT_RE.match(aria)
            or "next" in rel.lower().split()
        )
        if not matches:
            continue
        # Skip anchors that look like they point to a per-video page.
        if video_shape is not None and _url_shape(href) == video_shape:
            continue
        return href
    return None


def find_next_page(
    page: Page,
    current_url: str,
    *,
    selector: Optional[str] = None,
    pattern: Optional[str] = None,
    template: Optional[str] = None,
    next_page_number: Optional[int] = None,
    video_link_shape: Optional[Shape] = None,
) -> Optional[str]:
    """Discover the URL of the next listing page.

    Strategy precedence (first non-None wins):
      0. Explicit template (with `{n}`) — synthesize next_page_number.
      1. CSS selector override (`selector`).
      2. URL regex filter (`pattern`) — applied across all anchors.
      3. <link rel="next"> / <a rel="next">.
      4. Anchor heuristics (text/aria match) — skipping per-video anchors.
      5. URL-template inference on `current_url` (probe-validated).
    """
    self_url, _ = urldefrag(current_url)

    if template and next_page_number is not None:
        try:
            synth = template.format(n=next_page_number)
            abs_synth, _ = urldefrag(urljoin(current_url, synth))
            if abs_synth != self_url:
                return abs_synth
        except (KeyError, IndexError, ValueError) as e:
            logger.warning(f"Bad --page-url-template {template!r}: {e}")

    if selector:
        try:
            handle = page.query_selector(selector)
        except Exception as e:
            logger.warning(f"--next-selector {selector!r} failed: {e}")
            handle = None
        if handle:
            href = handle.get_attribute("href")
            if href:
                abs_url, _ = urldefrag(urljoin(current_url, href))
                if abs_url != self_url:
                    return abs_url

    records = _all_anchor_records(page, current_url)

    if pattern:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            logger.warning(f"Invalid --next-pattern {pattern!r}: {e}")
            regex = None
        if regex is not None:
            for r in records:
                if r["href"] != self_url and regex.search(r["href"]):
                    return r["href"]

    rel_next = _from_rel_next(page, current_url)
    if rel_next and rel_next != self_url:
        return rel_next

    anchor_match = _from_anchor_text(records, current_url, video_link_shape)
    if anchor_match:
        return anchor_match

    inferred = _increment_page_in_url(current_url)
    if inferred and inferred != self_url and _probe_url(inferred):
        return inferred

    return None


__all__ = [
    "extract_video_links",
    "find_next_page",
    "looks_like_listing",
    "_dominant_shape",
    "_url_shape",
]
