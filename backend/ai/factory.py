"""Builds the configured provider. This is the only place the provider is chosen."""
from __future__ import annotations

from functools import lru_cache

from django.conf import settings

from .provider import LLMConfigurationError, LLMProvider


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    name = settings.LLM_PROVIDER.lower()
    if name == "groq":
        from .groq_client import GroqProvider  # lazy: keeps `groq` import out of other paths

        return GroqProvider(
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            fast_model=settings.GROQ_FAST_MODEL or None,
            reasoning_effort=settings.GROQ_REASONING_EFFORT,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
        )
    raise LLMConfigurationError(f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'.")
