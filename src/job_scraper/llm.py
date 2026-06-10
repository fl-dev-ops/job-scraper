"""Shared LLM provider configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider: str
    api_token: str | None
    base_url: str | None

    def litellm_kwargs(self) -> dict[str, str]:
        kwargs = {"model": self.provider}
        if self.api_token:
            kwargs["api_key"] = self.api_token
        if self.base_url:
            kwargs["api_base"] = self.base_url
        return kwargs


def build_llm_config(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> ResolvedLLMConfig:
    llm_provider = (provider or os.getenv("LLM_PROVIDER") or "openrouter").lower()

    if llm_provider == "openrouter":
        resolved_model = (
            model
            or os.getenv("LLM_MODEL")
            or os.getenv("OPENROUTER_MODEL")
            or DEFAULT_OPENROUTER_MODEL
        )
        resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not resolved_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY missing. Set it in .env before running."
            )
        return ResolvedLLMConfig(
            provider=f"openrouter/{resolved_model}",
            api_token=resolved_api_key,
            base_url=None,
        )

    if llm_provider == "ollama":
        resolved_model = model or os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL")
        if not resolved_model:
            raise RuntimeError(
                "LLM_MODEL missing. Set it to a local Ollama model, for example llama3.1:8b."
            )
        return ResolvedLLMConfig(
            provider=f"ollama_chat/{resolved_model}",
            api_token=None,
            base_url=os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL,
        )

    raise RuntimeError(
        f"Unsupported LLM_PROVIDER={llm_provider!r}. Use 'openrouter' or 'ollama'."
    )
