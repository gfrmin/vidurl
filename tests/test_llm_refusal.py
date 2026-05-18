"""Tests for LLM refusal detection + abliterated-model fallback."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from vidurl.config import VideoExtractorConfig
from vidurl.llm import LLMExtractor


def _install_fake_scrapegraphai(run_results):
    """Install a fake scrapegraphai module whose graphs return run_results in order."""
    calls: list[dict] = []
    run_iter = iter(run_results)

    class FakeSmartScraperGraph:
        def __init__(self, prompt, source, config):
            calls.append({"prompt": prompt, "source": source, "config": config})

        def run(self):
            return next(run_iter)

    fake_module = types.ModuleType("scrapegraphai")
    fake_graphs = types.ModuleType("scrapegraphai.graphs")
    fake_graphs.SmartScraperGraph = FakeSmartScraperGraph
    fake_module.graphs = fake_graphs
    sys.modules["scrapegraphai"] = fake_module
    sys.modules["scrapegraphai.graphs"] = fake_graphs
    return calls


@pytest.fixture(autouse=True)
def _clean_fake_scrapegraphai():
    yield
    sys.modules.pop("scrapegraphai", None)
    sys.modules.pop("scrapegraphai.graphs", None)


def _ollama_config(fallback: str | None = None) -> VideoExtractorConfig:
    return VideoExtractorConfig(
        llm_provider="ollama",
        llm_model="qwen2.5:7b",
        llm_fallback_model=fallback,
    )


def test_refusal_text_routes_to_fallback_model():
    calls = _install_fake_scrapegraphai(
        run_results=[{}, {"video_url": "https://cdn.example.com/v.mp4"}]
    )
    extractor = LLMExtractor(_ollama_config(fallback="abliterated:7b"))

    raw_responses = iter([
        {"response": "I'm sorry, I can't help with that request."},
        {"response": "REFUSED"},
    ])

    def fake_post(url, json, timeout):
        resp = MagicMock()
        resp.json.return_value = next(raw_responses)
        resp.raise_for_status.return_value = None
        return resp

    with patch("requests.post", side_effect=fake_post):
        url = extractor.find_video_url("<html>...</html>", "https://example.com/p")

    assert url == "https://cdn.example.com/v.mp4"
    assert len(calls) == 2
    assert calls[0]["config"]["llm"]["model"] == "ollama/qwen2.5:7b"
    assert calls[1]["config"]["llm"]["model"] == "ollama/abliterated:7b"


def test_non_refusal_does_not_retry():
    calls = _install_fake_scrapegraphai(run_results=[{}])
    extractor = LLMExtractor(_ollama_config(fallback="abliterated:7b"))

    raw_responses = iter([
        {"response": '{"video_url": null}'},
        {"response": "COMPLIED"},
    ])

    def fake_post(url, json, timeout):
        resp = MagicMock()
        resp.json.return_value = next(raw_responses)
        resp.raise_for_status.return_value = None
        return resp

    with patch("requests.post", side_effect=fake_post):
        url = extractor.find_video_url("<html>...</html>", "https://example.com/p")

    assert url is None
    assert len(calls) == 1, "must not invoke fallback when model complied"


def test_no_fallback_config_short_circuits_probe():
    calls = _install_fake_scrapegraphai(run_results=[{}])
    extractor = LLMExtractor(_ollama_config(fallback=None))

    with patch("requests.post") as mock_post:
        url = extractor.find_video_url("<html>...</html>", "https://example.com/p")

    assert url is None
    assert len(calls) == 1
    mock_post.assert_not_called()


def test_non_ollama_provider_short_circuits_probe():
    calls = _install_fake_scrapegraphai(run_results=[{}])
    config = VideoExtractorConfig(
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5",
        llm_fallback_model="abliterated:7b",
    )
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        extractor = LLMExtractor(config)
        with patch("requests.post") as mock_post:
            url = extractor.find_video_url("<html>...</html>", "https://example.com/p")

    assert url is None
    assert len(calls) == 1
    mock_post.assert_not_called()


def test_primary_success_skips_probe():
    calls = _install_fake_scrapegraphai(
        run_results=[{"video_url": "https://cdn.example.com/v.mp4"}]
    )
    extractor = LLMExtractor(_ollama_config(fallback="abliterated:7b"))

    with patch("requests.post") as mock_post:
        url = extractor.find_video_url("<html>...</html>", "https://example.com/p")

    assert url == "https://cdn.example.com/v.mp4"
    assert len(calls) == 1
    mock_post.assert_not_called()
