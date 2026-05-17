# vidurl

**A more powerful, cleverer yt-dlp.** Given any web page — a single-video page, a thumbnail-grid listing, or something obscure yt-dlp doesn't know about — `vidurl` downloads the videos.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

## How it works

Three-tier escalation per URL, cheapest first:

1. **yt-dlp** handles whatever it can natively (thousands of sites, including playlists/channels).
2. **Playwright** loads the page and sniffs the rendered DOM, JS, network responses, and embedded iframes for a video URL — then downloads with `curl`. For listing pages, vidurl scrapes the DOM for video-page links and recurses.
3. **LLM** (optional, opt-in) — when the heuristics fail, vidurl can ask a large language model (via [scrapegraphai](https://github.com/ScrapeGraphAI/Scrapegraph-ai)) to identify the video URL or video-page links on the rendered DOM.

## Installation

```bash
uv sync
uv run playwright install chromium
```

`yt-dlp` and `playwright` are installed automatically. The LLM tier is optional:

```bash
uv sync --extra llm           # pulls in scrapegraphai
```

Also requires `curl` in `PATH`.

## Usage

```bash
# yt-dlp does the work
vidurl https://www.youtube.com/watch?v=dQw4w9WgXcQ

# Site yt-dlp doesn't know — Playwright finds the video
vidurl https://example.com/embedded-video-page

# Listing of videos — vidurl visits each link
vidurl https://example.com/gallery
vidurl https://example.com/gallery --listing --link-selector "a.video-card"

# Don't download, just print the commands
vidurl https://example.com/page --dry-run

# Enable LLM fallback
vidurl https://example.com/weird-page \
    --llm-provider anthropic --llm-model claude-haiku-4-5
```

## Flags

| Flag | Purpose |
|---|---|
| `--output-dir / -o DIR` | Where to save files (default: cwd) |
| `--dry-run` | Print the yt-dlp / curl commands instead of executing |
| `--no-ytdlp` | Skip the yt-dlp tier; go straight to Playwright |
| `--ytdlp-args 'STR'` | Extra args appended to the yt-dlp invocation |
| `--listing` | Force listing mode (skip per-page video extraction) |
| `--no-listing` | Never recurse into links |
| `--link-selector CSS` | Use this CSS selector to find video-page links |
| `--link-pattern REGEX` | Only follow links whose absolute URL matches |
| `--min-links N` | Minimum links for listing auto-detect (default 3) |
| `--llm-provider P` | `anthropic`, `openai`, `groq`, `google`, `ollama`, ... |
| `--llm-model M` | Model id |
| `--no-llm` | Disable the LLM tier even if provider/model are set |
| `--no-headless` | Show the browser window |
| `--timeout S` | Page load timeout (default 15s) |
| `--verbose / --quiet` | Logging |

## LLM tier

The LLM tier is **off by default**. To enable, pass both `--llm-provider` and `--llm-model` (or set them in `config.json`). API keys are read from environment first, then from gnome-keyring via `secret-tool` under `service=env, key=<PROVIDER_KEY>`. If a key is present but provider/model are not set, vidurl logs a hint and stays off — no silent spend.

## What's intentionally not (yet) supported

- **Pagination** across multiple listing pages — coming later.
- **Parallel downloads** of multiple listing-page videos — sequential for now.

## Configuration

Pass a JSON file via `--config path/to/config.json`. See `config.example.json` for all fields.

## Development

```bash
uv sync --extra dev
uv run pytest        # no tests yet
```

## License

AGPL-3.0-or-later. See `LICENSE`.
