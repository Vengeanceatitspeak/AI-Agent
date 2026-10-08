"""LLM provider factory — creates providers from config."""

from __future__ import annotations

from typing import Any

import structlog

from jarvis.config import AppConfig, ModelConfig, ProviderConfig
from jarvis.core.errors import LLMProviderNotFoundError
from jarvis.llm.base import LLMProvider
from jarvis.llm.fake import FakeLLMProvider

logger = structlog.get_logger()


def _create_provider(
    provider_name: str,
    provider_config: ProviderConfig,
    model_config: ModelConfig,
) -> Any:
    """Create an LLM provider instance from config.

    Returns an object implementing the LLMProvider protocol.
    """
    if provider_name == "anthropic":
        from jarvis.llm.anthropic import AnthropicProvider

        if not provider_config.api_key:
            raise LLMProviderNotFoundError(
                "Anthropic API key not set. Set ANTHROPIC_API_KEY in .env"
            )
        return AnthropicProvider(
            api_key=provider_config.api_key,
            model=model_config.model,
            max_tokens=model_config.max_tokens,
            temperature=model_config.temperature,
            max_retries=provider_config.max_retries,
            timeout=float(provider_config.timeout_seconds),
        )

    elif provider_name == "gemini":
        from jarvis.llm.gemini import GeminiProvider

        if not provider_config.api_key:
            raise LLMProviderNotFoundError(
                "Google API key not set. Set GOOGLE_API_KEY in .env"
            )
        return GeminiProvider(
            api_key=provider_config.api_key,
            model=model_config.model,
            max_tokens=model_config.max_tokens,
            temperature=model_config.temperature,
            timeout=float(provider_config.timeout_seconds),
        )

    elif provider_name == "openai":
        from jarvis.llm.openai_compat import OpenAICompatProvider

        if not provider_config.api_key:
            raise LLMProviderNotFoundError(
                "OpenAI API key not set. Set OPENAI_API_KEY in .env"
            )
        return OpenAICompatProvider(
            api_key=provider_config.api_key,
            model=model_config.model,
            max_tokens=model_config.max_tokens,
            temperature=model_config.temperature,
            base_url=provider_config.base_url,
            max_retries=provider_config.max_retries,
            timeout=float(provider_config.timeout_seconds),
        )

    elif provider_name == "ollama":
        from jarvis.llm.ollama import OllamaProvider

        base_url = provider_config.base_url or "http://localhost:11434"
        return OllamaProvider(
            model=model_config.model,
            base_url=base_url,
            max_tokens=model_config.max_tokens,
            temperature=model_config.temperature,
            timeout=float(provider_config.timeout_seconds),
        )

    elif provider_name == "groq":
        from jarvis.llm.groq import GroqProvider

        return GroqProvider(
            model=model_config.model,
            max_tokens=model_config.max_tokens,
            temperature=model_config.temperature,
            max_retries=provider_config.max_retries,
            timeout=float(provider_config.timeout_seconds),
        )

    elif provider_name == "fake":
        return FakeLLMProvider(model=model_config.model)

    else:
        raise LLMProviderNotFoundError(
            f"Unknown LLM provider: '{provider_name}'. "
            f"Available: anthropic, gemini, openai, ollama, groq, fake"
        )


class LLMFactory:
    """Creates and caches LLM provider instances from config.

    Usage:
        factory = LLMFactory(config)
        reasoning = factory.get_reasoning_model()
        router = factory.get_router_model()
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._cache: dict[str, Any] = {}

    def _get_provider_config(self, provider_name: str) -> ProviderConfig:
        """Get provider config, with fallback to empty config."""
        return self._config.jarvis.providers.get(
            provider_name, ProviderConfig()
        )

    def _get_or_create(self, role: str, model_config: ModelConfig) -> Any:
        """Get cached provider or create a new one."""
        cache_key = f"{role}:{model_config.provider}:{model_config.model}"
        if cache_key not in self._cache:
            provider_config = self._get_provider_config(model_config.provider)
            self._cache[cache_key] = _create_provider(
                model_config.provider, provider_config, model_config
            )
            logger.info(
                "llm_provider_created",
                role=role,
                provider=model_config.provider,
                model=model_config.model,
            )
        return self._cache[cache_key]

    def get_reasoning_model(self) -> Any:
        """Get the reasoning (main) model provider."""
        return self._get_or_create(
            "reasoning", self._config.jarvis.models.reasoning_model
        )

    def get_router_model(self) -> Any:
        """Get the router (fast) model provider."""
        return self._get_or_create(
            "router", self._config.jarvis.models.router_model
        )

    def get_summarizer_model(self) -> Any:
        """Get the summarizer model provider."""
        return self._get_or_create(
            "summarizer", self._config.jarvis.models.summarizer_model
        )

    def get_provider_for_config(self, model_config: ModelConfig) -> Any:
        """Get a provider for an arbitrary model config."""
        return self._get_or_create("custom", model_config)
