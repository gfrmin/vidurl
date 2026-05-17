"""
Listing-page detection and link extraction.

Given a Playwright page, find candidate links that look like individual video
pages — either via explicit selector/pattern, or auto-detect by grouping links
by URL "shape" (path prefix + query keys) and taking the largest group.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag

from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def _all_anchor_hrefs(page: Page, base_url: str) -> list[str]:
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
    except Exception as e:
        logger.debug(f"Failed to collect anchors: {e}")
        return []
    normalized: list[str] = []
    for href in hrefs:
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        abs_url, _ = urldefrag(abs_url)
        if not abs_url.startswith(("http://", "https://")):
            continue
        normalized.append(abs_url)
    return normalized


def _url_shape(url: str) -> tuple[str, str, tuple[str, ...]]:
    """Group key: (netloc, path-template-without-last-segment, sorted query keys).

    A typical listing has many links of shape /watch/<id>, /video/<slug>, etc.
    Stripping the last path segment groups those together.
    """
    p = urlparse(url)
    path_parts = [seg for seg in p.path.split("/") if seg]
    template = "/" + "/".join(path_parts[:-1]) if len(path_parts) > 1 else "/"
    query_keys = tuple(sorted({kv.split("=")[0] for kv in p.query.split("&") if kv}))
    return (p.netloc, template, query_keys)


def extract_video_links(
    page: Page,
    base_url: str,
    selector: Optional[str] = None,
    pattern: Optional[str] = None,
    min_links: int = 3,
) -> list[str]:
    """Extract candidate video-page links from the loaded page.

    - If `selector` is given, scrape href from matching elements.
    - Else if `pattern` is given, filter all anchors by regex.
    - Else auto-detect via URL-shape grouping.
    """
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

    # Auto-detect.
    if not all_hrefs:
        return []

    shape_counts: Counter[tuple[str, str, tuple[str, ...]]] = Counter()
    by_shape: dict[tuple[str, str, tuple[str, ...]], list[str]] = {}
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


__all__ = ["extract_video_links", "looks_like_listing"]
