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

### Quick — run from PyPI with `uvx`

No clone, no install step:

```bash
uvx --from vidurl playwright install chromium   # one-time: install the browser
uvx vidurl <URL>                                 # run from PyPI in an ephemeral env
```

For the LLM tier (pulls in scrapegraphai on the fly):

```bash
uvx --from 'vidurl[llm]' vidurl <URL> \
    --llm-provider ollama --llm-model qwen2.5:7b-instruct
```

Playwright caches its browser binaries under `~/.cache/ms-playwright/`, so the one-time `playwright install chromium` is shared across `uvx` invocations.

### Persistent install

```bash
uv tool install vidurl            # or: pip install vidurl
playwright install chromium
```

With the LLM extra: `uv tool install 'vidurl[llm]'` (or `pip install 'vidurl[llm]'`).

### Development clone

```bash
git clone https://github.com/gfrmin/vidurl
cd vidurl
uv sync                          # add --extra llm to include scrapegraphai
uv run playwright install chromium
```

`yt-dlp` and `playwright` are installed automatically. `curl` must also be on `PATH`.

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
| `--max-pages N` | Max listing pages to walk (default 10) |
| `--no-paginate` | Disable pagination — process only the first listing page |
| `--next-selector CSS` | CSS selector for the next-page link |
| `--next-pattern REGEX` | Regex; treat as next-page link only if URL matches |
| `--page-url-template URL` | URL template with `{n}`; vidurl walks 2..max-pages |
| `--llm-provider P` | `anthropic`, `openai`, `groq`, `google`, `ollama`, ... |
| `--llm-model M` | Model id |
| `--no-llm` | Disable the LLM tier even if provider/model are set |
| `--yes / -y` | Accept the auto-detected LLM pick without prompting |
| `--no-headless` | Show the browser window |
| `--timeout S` | Page load timeout (default 15s) |
| `--verbose / --quiet` | Logging |

## LLM tier

The LLM tier is **off by default**. To enable, pass both `--llm-provider` and `--llm-model` (or set them in `config.json`). API keys are read from environment first, then from gnome-keyring via `secret-tool` under `service=env, key=<PROVIDER_KEY>`. If a key is present but provider/model are not set, vidurl logs a hint and stays off — no silent spend.

If you installed the `llm` extra (`pip install 'vidurl[llm]'`) and don't pass `--llm-provider`/`--llm-model`, vidurl auto-detects an available backend and asks before using it. A local Ollama install is preferred over cloud providers; among installed Ollama models, vidurl skips embedding and vision-language families and picks the largest text LLM by parameter count. Pass `-y` to accept the pick without prompting, or `--no-llm` to skip detection entirely. Non-TTY runs (pipes, cron) skip the prompt silently unless `-y` is set.

## Pagination

When a listing page is detected, vidurl walks subsequent pages automatically (capped by `--max-pages`, default 10). Next-page discovery tries, in order:

1. `<link rel="next">` or `<a rel="next">`.
2. Anchor text or `aria-label` matching `Next`, `›`, `→`, `»`, `More` (filtered to avoid "next video" navigation).
3. URL-template inference (`?page=N`, `/page/N/`, `&offset=N`, trailing `/N`), validated with a HEAD/GET probe.
4. LLM fallback (if the LLM tier is enabled).

Override with `--next-selector`, `--next-pattern`, or `--page-url-template URL` (e.g. `https://example.com/list?page={n}`). Disable with `--no-paginate`.

## What's intentionally not (yet) supported

- **"Load more" buttons** and **infinite scroll** — these need click-and-wait logic without a URL change.
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
