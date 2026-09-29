from types import SimpleNamespace

import groq
import httpx
from django.test import SimpleTestCase

from ai.groq_client import GroqProvider
from ai.provider import (
    LLMConfigurationError,
    LLMResponseError,
    LLMTransientError,
    ModelTier,
)
from ai.schemas import LLMSmokeTest


def _response(content='{"status":"ok","echo":"x"}', finish="stop", refusal=None):
    return SimpleNamespace(
        id="req_123",
        choices=[SimpleNamespace(
            finish_reason=finish,
            message=SimpleNamespace(content=content, refusal=refusal),
        )],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class FakeClient:
    """Stands in for groq.Groq; records requests, returns/raises what we script."""

    def __init__(self, outcome):
        self.calls = []
        self._outcome = outcome
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _provider(client, model="openai/gpt-oss-120b", fast="openai/gpt-oss-20b"):
    return GroqProvider(api_key="test", model=model, fast_model=fast, client=client)


def _call(provider, **kw):
    return provider.generate_structured(system="s", user="u", schema=LLMSmokeTest, **kw)


def _http_error(cls, status):
    req = httpx.Request("POST", "https://api.groq.com/x")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


class GroqProviderTests(SimpleTestCase):
    def test_happy_path_sends_strict_schema_and_parses(self):
        client = FakeClient(_response())
        result = _call(_provider(client))
        self.assertEqual(result.data.echo, "x")
        self.assertEqual(result.usage.total_tokens, 15)
        self.assertEqual(result.request_id, "req_123")
        req = client.calls[0]
        js = req["response_format"]["json_schema"]
        self.assertTrue(js["strict"])
        self.assertIs(js["schema"]["additionalProperties"], False)
        self.assertEqual(req["model"], "openai/gpt-oss-120b")
        self.assertIs(req["include_reasoning"], False)
        self.assertEqual(req["reasoning_effort"], "medium")

    def test_fast_tier_uses_fast_model_and_low_effort(self):
        client = FakeClient(_response())
        _call(_provider(client), tier=ModelTier.FAST)
        self.assertEqual(client.calls[0]["model"], "openai/gpt-oss-20b")
        self.assertEqual(client.calls[0]["reasoning_effort"], "low")

    def test_reasoning_params_omitted_for_non_gpt_oss(self):
        client = FakeClient(_response())
        _call(_provider(client, model="some/other-model", fast=None))
        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertNotIn("include_reasoning", client.calls[0])

    def test_invalid_content_raises_response_error(self):
        with self.assertRaises(LLMResponseError):
            _call(_provider(FakeClient(_response(content='{"status":"nope","echo":1}'))))

    def test_non_json_content_raises_response_error(self):
        with self.assertRaises(LLMResponseError):
            _call(_provider(FakeClient(_response(content="not json"))))

    def test_truncation_raises_response_error(self):
        with self.assertRaises(LLMResponseError):
            _call(_provider(FakeClient(_response(finish="length"))))

    def test_refusal_raises_response_error(self):
        with self.assertRaises(LLMResponseError):
            _call(_provider(FakeClient(_response(refusal="no"))))

    def test_rate_limit_maps_to_transient(self):
        client = FakeClient(_http_error(groq.RateLimitError, 429))
        with self.assertRaises(LLMTransientError):
            _call(_provider(client))

    def test_auth_error_maps_to_configuration(self):
        client = FakeClient(_http_error(groq.AuthenticationError, 401))
        with self.assertRaises(LLMConfigurationError):
            _call(_provider(client))

    def test_bad_request_maps_to_response_error(self):
        client = FakeClient(_http_error(groq.BadRequestError, 400))
        with self.assertRaises(LLMResponseError):
            _call(_provider(client))

    def test_missing_api_key_raises(self):
        with self.assertRaises(LLMConfigurationError):
            GroqProvider(api_key="", model="m")
