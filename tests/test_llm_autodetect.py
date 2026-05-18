"""Tests for the Ollama model auto-detection, including refusal-fallback picking."""

from __future__ import annotations

from vidurl.llm_autodetect import pick_best_ollama_model, pick_fallback_ollama_model


def _model(name: str, size: str = "7B") -> dict:
    return {"name": name, "parameter_size": size, "family": "qwen", "modified_at": ""}


def test_picks_largest_text_model_as_primary():
    models = [
        _model("nomic-embed-text:latest", "137M"),
        _model("qwen2.5:7b-instruct", "7.6B"),
        _model("qwen2.5:14b", "14B"),
    ]
    # nomic should be filtered as embedding
    assert pick_best_ollama_model(models) == "qwen2.5:14b"


def test_fallback_finds_abliterated_model():
    models = [
        _model("qwen2.5:7b-instruct", "7.6B"),
        _model("huihui_ai/qwen2.5-abliterate:7b", "7.6B"),
    ]
    primary = pick_best_ollama_model(models)
    fallback = pick_fallback_ollama_model(models, primary=primary)
    assert fallback == "huihui_ai/qwen2.5-abliterate:7b"


def test_fallback_none_when_no_uncensored_installed():
    models = [
        _model("qwen2.5:7b-instruct", "7.6B"),
        _model("llama3.1:8b", "8B"),
    ]
    assert pick_fallback_ollama_model(models, primary="qwen2.5:7b-instruct") is None


def test_fallback_prefers_largest_uncensored_and_excludes_primary():
    models = [
        _model("dolphin-llama3:8b", "8B"),
        _model("huihui_ai/qwen2.5-abliterate:14b", "14B"),
        _model("dolphin-mistral:7b", "7B"),
    ]
    # If somehow the primary is itself an uncensored model, don't return it.
    fallback = pick_fallback_ollama_model(models, primary="huihui_ai/qwen2.5-abliterate:14b")
    assert fallback == "dolphin-llama3:8b"
