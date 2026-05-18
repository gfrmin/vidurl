"""
Command-line interface for vidurl.
"""

from __future__ import annotations

import argparse
import logging
import shlex
import sys

from .config import VideoExtractorConfig
from .exceptions import VideoExtractorError
from .pipeline import Pipeline
from .utils import load_config_from_file, setup_logging


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract and download videos from web pages — yt-dlp first, with Playwright fallback and optional LLM-powered extraction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s https://www.youtube.com/watch?v=...        # yt-dlp downloads
  %(prog)s https://example.com/video-page             # Playwright fallback
  %(prog)s https://example.com/gallery --listing      # crawl listing
  %(prog)s https://example.com/page --dry-run         # print commands only
        """,
    )
    parser.add_argument("url", help="URL of the web page (video page or listing)")

    parser.add_argument("--output-dir", "-o", default=".",
                        help="Output directory (default: current dir)")
    parser.add_argument("--filename", "-f",
                        help="Default filename when one can't be inferred")

    parser.add_argument("--timeout", type=int, default=15,
                        help="Page load timeout in seconds (default: 15)")
    parser.add_argument("--curl-timeout", type=int, default=15,
                        help="Per-request curl timeout in seconds (default: 15)")

    parser.add_argument("--no-headless", action="store_true",
                        help="Run browser with a visible window")
    parser.add_argument("--user-agent",
                        help="Override User-Agent string")
    parser.add_argument("--window-size", default="1920,1080",
                        help="Viewport size WxH (default: 1920,1080)")

    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=4,
                        help="Parallel validation threads (default: 4)")

    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print yt-dlp / curl commands instead of downloading")

    parser.add_argument("--config", type=str,
                        help="Path to JSON configuration file")

    # yt-dlp control
    parser.add_argument("--no-ytdlp", action="store_true",
                        help="Skip the yt-dlp tier; go straight to Playwright")
    parser.add_argument("--ytdlp-args", type=str, default="",
                        help="Extra args passed verbatim to yt-dlp (shell-quoted string)")

    # Listing control
    parser.add_argument("--listing", action="store_true",
                        help="Force listing mode: skip in-page video extraction")
    parser.add_argument("--no-listing", action="store_true",
                        help="Never recurse into links even if no video found")
    parser.add_argument("--link-selector", type=str,
                        help="CSS selector for video-page links on a listing")
    parser.add_argument("--link-pattern", type=str,
                        help="Regex; only follow links whose absolute URL matches")
    parser.add_argument("--min-links", type=int, default=3,
                        help="Min links for listing auto-detect (default: 3)")

    # Pagination
    parser.add_argument("--max-pages", type=int, default=10,
                        help="Max listing pages to walk (default: 10)")
    parser.add_argument("--no-paginate", action="store_true",
                        help="Disable pagination; process only the first listing page")
    parser.add_argument("--next-selector", type=str,
                        help="CSS selector for the next-page link")
    parser.add_argument("--next-pattern", type=str,
                        help="Regex; treat as next-page link only if URL matches")
    parser.add_argument("--page-url-template", type=str,
                        help='URL template containing "{n}"; vidurl substitutes 2..max-pages')

    # LLM control
    parser.add_argument("--llm-provider", type=str,
                        help="LLM provider (e.g. anthropic, openai, ollama)")
    parser.add_argument("--llm-model", type=str,
                        help="LLM model id (e.g. claude-haiku-4-5)")
    parser.add_argument("--llm-fallback-model", type=str,
                        help="Local Ollama model to retry with when the primary LLM "
                             "refuses (e.g. huihui_ai/qwen2.5-abliterate:7b). "
                             "Only used when --llm-provider is ollama.")
    parser.add_argument("--no-llm", action="store_true",
                        help="Disable the LLM tier even if provider/model are set")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Accept auto-detected LLM pick without prompting")

    args = parser.parse_args()
    if args.quiet and args.verbose:
        parser.error("--quiet and --verbose are mutually exclusive")
    if args.listing and args.no_listing:
        parser.error("--listing and --no-listing are mutually exclusive")
    return args


def _parse_viewport(window_size: str) -> tuple[int, int]:
    try:
        w, h = window_size.split(",")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 1920, 1080


def create_config(args: argparse.Namespace) -> VideoExtractorConfig:
    config_dict: dict = {}
    if args.config:
        config_dict.update(load_config_from_file(args.config))

    viewport_w, viewport_h = _parse_viewport(args.window_size)

    overrides = {
        "page_load_timeout": args.timeout,
        "curl_timeout": args.curl_timeout,
        "video_detection_timeout": args.timeout * 3,
        "headless": not args.no_headless,
        "viewport_width": viewport_w,
        "viewport_height": viewport_h,
        "output_dir": args.output_dir,
        "verbose": args.verbose,
        "quiet": args.quiet,
        "max_retries": args.max_retries,
        "max_workers": args.parallel,
        "enable_ytdlp": not args.no_ytdlp,
        "ytdlp_extra_args": shlex.split(args.ytdlp_args) if args.ytdlp_args else [],
        "force_listing": args.listing,
        "disable_listing": args.no_listing,
        "link_selector": args.link_selector,
        "link_pattern": args.link_pattern,
        "listing_min_links": args.min_links,
        "enable_pagination": not args.no_paginate,
        "max_pages": args.max_pages,
        "next_selector": args.next_selector,
        "next_pattern": args.next_pattern,
        "page_url_template": args.page_url_template,
        "llm_provider": args.llm_provider,
        "llm_model": args.llm_model,
        "llm_fallback_model": args.llm_fallback_model,
        "disable_llm": args.no_llm,
    }
    if args.user_agent:
        overrides["user_agent"] = args.user_agent
    if args.filename:
        overrides["default_filename"] = args.filename

    config_dict.update({k: v for k, v in overrides.items() if v is not None})

    if (not args.no_llm
            and not config_dict.get("disable_llm")
            and not config_dict.get("llm_provider")
            and not config_dict.get("llm_model")):
        from .llm_autodetect import scrapegraphai_installed, detect_top_pick, confirm_pick
        if scrapegraphai_installed():
            pick = detect_top_pick()
            if pick and confirm_pick(*pick, assume_yes=args.yes, quiet=args.quiet):
                provider, model, auto_fallback = pick
                config_dict["llm_provider"] = provider
                config_dict["llm_model"] = model
                if auto_fallback and not config_dict.get("llm_fallback_model"):
                    config_dict["llm_fallback_model"] = auto_fallback

    return VideoExtractorConfig(**config_dict)


def main() -> None:
    args = parse_arguments()
    config = create_config(args)
    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info(f"Starting vidurl for: {args.url}")
    if config.llm_provider and config.llm_model and not config.disable_llm:
        logger.info(f"LLM tier enabled: {config.llm_provider}/{config.llm_model}")
    elif (config.llm_provider is None) != (config.llm_model is None):
        logger.warning("Both --llm-provider and --llm-model are required to enable the LLM tier")

    try:
        with Pipeline(config, dry_run=args.dry_run) as pipeline:
            success = pipeline.process(args.url)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(130)
    except VideoExtractorError as e:
        logger.error(f"vidurl failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)

    if not config.quiet:
        if pipeline.result.successes:
            print(f"\nProcessed {len(pipeline.result.successes)} video(s) successfully.")
        if pipeline.result.failures:
            print(f"Failed: {len(pipeline.result.failures)}")
            for url, reason in pipeline.result.failures:
                print(f"  - {url}: {reason}")

    if not success and not pipeline.result.successes:
        sys.exit(1)


if __name__ == "__main__":
    main()
