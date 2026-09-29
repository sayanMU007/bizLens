from django.test import SimpleTestCase, override_settings

from ai.factory import get_llm_provider
from ai.groq_client import GroqProvider
from ai.provider import LLMConfigurationError


class FactoryTests(SimpleTestCase):
    def setUp(self):
        get_llm_provider.cache_clear()
        self.addCleanup(get_llm_provider.cache_clear)

    @override_settings(LLM_PROVIDER="nope")
    def test_unknown_provider(self):
        with self.assertRaises(LLMConfigurationError):
            get_llm_provider()

    @override_settings(LLM_PROVIDER="groq", GROQ_API_KEY="")
    def test_groq_without_key(self):
        with self.assertRaises(LLMConfigurationError):
            get_llm_provider()

    @override_settings(LLM_PROVIDER="groq", GROQ_API_KEY="gsk_test")
    def test_groq_with_key(self):
        self.assertIsInstance(get_llm_provider(), GroqProvider)
