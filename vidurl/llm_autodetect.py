"""
Auto-detect available LLM providers and pick a sensible default.

Triggered from the CLI when the `llm` extra is installed but the user didn't
specify --llm-provider/--llm-model. Ollama is preferred over cloud providers
when available, because a local model means no API spend and no key plumbing.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from typing import Optional

import requests

from .llm import PROVIDER_KEY_ENV, _resolve_api_key

logger = logging.getLogger(__name__)


# Default cloud models — small, cheap, fast. Picked to keep first-run cost low.
CLOUD_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "google": "gemini-1.5-flash",
    "groq": "llama-3.1-8b-instant",
}

CLOUD_PRIORITY: tuple[str, ...] = ("anthropic", "openai", "google", "groq")

OLLAMA_HOST = "http://localhost:11434"


def scrapegraphai_installed() -> bool:
    return importlib.util.find_spec("scrapegraphai") is not None


def _parse_param_size(s: str) -> float:
    """Parse Ollama's parameter_size strings like '9.7B', '137M', '1.5T'."""
    if not isinstance(s, str) or not s:
        return 0.0
    s = s.strip().upper()
    suffix = s[-1]
    multipliers = {"T": 1000.0, "B": 1.0, "M": 0.001, "K": 0.000001}
    if suffix in multipliers:
        try:
            return float(s[:-1]) * multipliers[suffix]
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _models_from_ollama_http(timeout: float = 2.0) -> Optional[list[dict]]:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"Ollama HTTP probe failed: {e}")
        return None
    models = data.get("models")
    if not isinstance(models, list):
        return None
    return [_normalize_model(m) for m in models if isinstance(m, dict)]


def _models_from_ollama_cli(timeout: float = 3.0) -> Optional[list[dict]]:
    try:
        result = subprocess.run(
            ["ollama", "list", "--json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        logger.debug(f"Ollama CLI probe failed: {e}")
        return None
    if result.returncode != 0:
        return None
    out: list[dict] = []
    # `ollama list --json` may emit either one JSON object per line or a single
    # JSON array, depending on version. Try both.
    text = result.stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        candidates = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        candidates = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError:
                return None
    for c in candidates:
        if isinstance(c, dict):
            out.append(_normalize_model(c))
    return out or None


def _normalize_model(raw: dict) -> dict:
    """Flatten the /api/tags shape to the keys we use downstream."""
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    return {
        "name": raw.get("name") or raw.get("model") or "",
        "parameter_size": (details.get("parameter_size") if details else None)
                          or raw.get("parameter_size")
                          or "",
        "family": (details.get("family") if details else None) or raw.get("family") or "",
        "modified_at": raw.get("modified_at") or "",
    }


def list_ollama_models(timeout: float = 2.0) -> Optional[list[dict]]:
    """Return installed Ollama models, or None if Ollama is unreachable."""
    via_http = _models_from_ollama_http(timeout)
    if via_http is not None:
        return via_http
    return _models_from_ollama_cli()


def _is_embedding_model(m: dict) -> bool:
    family = (m.get("family") or "").lower()
    name = (m.get("name") or "").lower()
    return "bert" in family or "embed" in family or "embed" in name


def _is_vision_model(m: dict) -> bool:
    family = (m.get("family") or "").lower()
    return family.endswith("vl") or family in {"llava", "bakllava", "moondream"}


# Substrings (case-insensitive) we treat as evidence a model has had its safety
# training removed / weakened. These are the models we want to use as a fallback
# when the primary aligned model refuses to extract URLs.
UNCENSORED_MARKERS: tuple[str, ...] = (
    "abliterate",
    "abliterated",
    "uncensored",
    "huihui",
    "dolphin",
)


def _usable_models(models: list[dict]) -> list[dict]:
    return [
        m for m in models
        if m.get("name") and not _is_embedding_model(m) and not _is_vision_model(m)
    ]


def _is_uncensored(m: dict) -> bool:
    name = (m.get("name") or "").lower()
    return any(marker in name for marker in UNCENSORED_MARKERS)


def pick_best_ollama_model(models: list[dict]) -> Optional[str]:
    usable = _usable_models(models)
    if not usable:
        return None
    usable.sort(
        key=lambda m: (_parse_param_size(m.get("parameter_size", "")), m.get("modified_at", "")),
        reverse=True,
    )
    return usable[0]["name"]


def pick_fallback_ollama_model(models: list[dict], primary: Optional[str]) -> Optional[str]:
    """Find an uncensored/abliterated model among installed Ollama models, if any.

    Returned model is the largest uncensored model that isn't the primary.
    """
    usable = [m for m in _usable_models(models) if _is_uncensored(m) and m.get("name") != primary]
    if not usable:
        return None
    usable.sort(
        key=lambda m: (_parse_param_size(m.get("parameter_size", "")), m.get("modified_at", "")),
        reverse=True,
    )
    return usable[0]["name"]


def detect_top_pick() -> Optional[tuple[str, str, Optional[str]]]:
    """Return the (provider, model, fallback_model) we'd recommend, or None.

    `fallback_model` is set only for Ollama, when an uncensored/abliterated model
    is installed alongside the primary pick.
    """
    models = list_ollama_models()
    if models:
        best = pick_best_ollama_model(models)
        if best:
            return ("ollama", best, pick_fallback_ollama_model(models, primary=best))

    for provider in CLOUD_PRIORITY:
        if provider not in PROVIDER_KEY_ENV:
            continue
        if _resolve_api_key(provider):
            return (provider, CLOUD_DEFAULT_MODEL[provider], None)

    return None


def confirm_pick(
    provider: str,
    model: str,
    fallback: Optional[str] = None,
    *,
    assume_yes: bool,
    quiet: bool = False,
) -> bool:
    """Ask the user whether to use the auto-detected provider/model.

    Returns True if accepted. Non-TTY (or quiet mode) without -y → False, silently.
    """
    label = f"{provider}/{model}"
    if fallback:
        label += f" (with {provider}/{fallback} as refusal fallback)"
    if assume_yes:
        return True
    if quiet or not sys.stdin.isatty():
        logger.info(
            f"LLM auto-detection found {label} but stdin is not a TTY "
            f"(pass -y to accept automatically)"
        )
        return False
    prompt = f"LLM tier available: {label} — use it? [Y/n] "
    try:
        sys.stderr.write(prompt)
        sys.stderr.flush()
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\n")
        return False
    return answer in ("", "y", "yes")


__all__ = [
    "scrapegraphai_installed",
    "list_ollama_models",
    "pick_best_ollama_model",
    "pick_fallback_ollama_model",
    "detect_top_pick",
    "confirm_pick",
]
