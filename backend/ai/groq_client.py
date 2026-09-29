"""Groq implementation of `LLMProvider`. The only module that imports `groq`."""
from __future__ import annotations

import logging
import time

import groq
from groq import Groq
from pydantic import ValidationError

from .provider import (
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMResponseError,
    LLMTransientError,
    LLMUsage,
    ModelTier,
    StructuredResult,
    T,
)
from .strict_schema import StrictSchemaError, to_strict_json_schema

logger = logging.getLogger("bizlens.ai")

# `reasoning_effort` / `include_reasoning` are sent only to GPT-OSS models.
# Other reasoning families (e.g. Qwen) use different reasoning parameters.
_GPT_OSS_PREFIX = "openai/gpt-oss"


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        fast_model: str | None = None,
        reasoning_effort: str = "medium",
        default_temperature: float = 0.2,
        default_max_output_tokens: int = 8192,
        timeout: float = 30.0,
        max_retries: int = 2,
        client: Groq | None = None,
    ) -> None:
        if not api_key and client is None:
            raise LLMConfigurationError("GROQ_API_KEY is not set in the backend environment.")
        self._client = client or Groq(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._models = {ModelTier.SMART: model, ModelTier.FAST: fast_model or model}
        self._reasoning_effort = {ModelTier.SMART: reasoning_effort, ModelTier.FAST: "low"}
        self._default_temperature = default_temperature
        self._default_max_output_tokens = default_max_output_tokens

    def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tier: ModelTier = ModelTier.SMART,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> StructuredResult[T]:
        model = self._models[tier]
        try:
            json_schema = to_strict_json_schema(schema)
        except StrictSchemaError as exc:
            raise LLMConfigurationError(str(exc)) from exc

        request: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._default_temperature if temperature is None else temperature,
            # For reasoning models, reasoning tokens count toward this limit.
            "max_completion_tokens": max_output_tokens or self._default_max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": json_schema},
            },
        }
        if model.startswith(_GPT_OSS_PREFIX):
            request["reasoning_effort"] = self._reasoning_effort[tier]
            request["include_reasoning"] = False  # we never store or show chain-of-thought

        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(**request)
        except (groq.AuthenticationError, groq.PermissionDeniedError) as exc:
            raise LLMConfigurationError(f"Groq rejected the credentials: {exc}") from exc
        except groq.BadRequestError as exc:
            raise LLMResponseError(f"Groq rejected the request or schema: {exc}") from exc
        except (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError) as exc:
            # APITimeoutError is a subclass of APIConnectionError.
            raise LLMTransientError(f"Groq temporarily unavailable: {exc.__class__.__name__}") from exc
        except groq.APIStatusError as exc:
            raise LLMError(f"Groq API error {exc.status_code}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not response.choices:
            raise LLMResponseError("Groq returned no choices.")
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMResponseError("The model refused to answer.")
        if choice.finish_reason == "length":
            raise LLMResponseError(
                "Response truncated (max_output_tokens too low for this reasoning effort)."
            )
        content = choice.message.content
        if not content:
            raise LLMResponseError("The model returned an empty response.")

        try:
            data = schema.model_validate_json(content)  # the check BizLens relies on
        except ValidationError as exc:
            raise LLMResponseError(
                f"Response failed {schema.__name__} validation ({exc.error_count()} errors)."
            ) from exc

        usage = response.usage
        result = StructuredResult(
            data=data,
            provider=self.name,
            model=model,
            usage=LLMUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
            ),
            latency_ms=latency_ms,
            request_id=getattr(response, "id", None),
        )
        # Metadata only: prompts/responses may contain customer data.
        logger.info(
            "llm_call provider=%s model=%s schema=%s latency_ms=%d tokens=%d request_id=%s",
            self.name, model, schema.__name__, latency_ms,
            result.usage.total_tokens, result.request_id,
        )
        return result
