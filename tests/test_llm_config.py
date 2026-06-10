from __future__ import annotations

import pytest

from job_scraper.llm import build_llm_config


def test_openrouter_config_is_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    config = build_llm_config()

    assert config.provider == "openrouter/deepseek/deepseek-v4-flash"
    assert config.api_token == "sk-test"
    assert config.base_url is None


def test_ollama_config_uses_local_base_url_without_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1:8b")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    config = build_llm_config()

    assert config.provider == "ollama_chat/llama3.1:8b"
    assert config.api_token is None
    assert config.base_url == "http://localhost:11434"


def test_openrouter_still_requires_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY missing"):
        build_llm_config()
