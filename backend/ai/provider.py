"""Provider-agnostic LLM interface.

The decision engine (planner, SQL generator, narrator) depends ONLY on this
module. Nothing outside `ai/groq_client.py` may import the `groq` package.
Swapping providers means writing one new subclass of `LLMProvider` and
changing `LLM_PROVIDER` in the environment.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ModelTier(str, Enum):
    """Logical model size. Each provider maps a tier to a concrete model id."""

    SMART = "smart"  # SQL generation, planning, evidence-grounded narration
    FAST = "fast"  # cheap classification / question understanding


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    """A validated structured response plus the metadata we persist for lineage."""

    data: T
    provider: str
    model: str
    usage: LLMUsage
    latency_ms: int
    request_id: str | None = None


class LLMError(Exception):
    """Base class for all LLM failures."""


class LLMConfigurationError(LLMError):
    """Missing/invalid credentials or unknown provider. Not retryable."""


class LLMTransientError(LLMError):
    """Rate limit, timeout, connection or 5xx error (after SDK-level retries)."""


class LLMResponseError(LLMError):
    """The model answered, but not with a usable, schema-valid response."""


class LLMProvider(abc.ABC):
    name: str

    @abc.abstractmethod
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
        """Return a response validated against the Pydantic `schema`.

        Implementations must raise only `LLMError` subclasses, and must never
        log prompt or response content (it may contain customer business data).
        """
