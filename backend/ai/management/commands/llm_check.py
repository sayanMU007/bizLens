import secrets

from django.core.management.base import BaseCommand, CommandError

from ai.factory import get_llm_provider
from ai.prompts import load_prompt
from ai.provider import LLMError, ModelTier
from ai.schemas import LLMSmokeTest


class Command(BaseCommand):
    help = "Make one live structured-output call to the configured LLM provider."

    def add_arguments(self, parser):
        parser.add_argument("--tier", choices=[t.value for t in ModelTier], default="smart")

    def handle(self, *args, **opts):
        token = f"bizlens-{secrets.token_hex(4)}"
        try:
            result = get_llm_provider().generate_structured(
                system=load_prompt("smoke_test"),
                user=token,
                schema=LLMSmokeTest,
                tier=ModelTier(opts["tier"]),
                max_output_tokens=1024,
            )
        except LLMError as exc:
            raise CommandError(f"{exc.__class__.__name__}: {exc}") from exc

        if result.data.echo != token:
            raise CommandError(f"Schema-valid but wrong content: {result.data.echo!r} != {token!r}")
        self.stdout.write(self.style.SUCCESS(
            f"OK provider={result.provider} model={result.model} "
            f"latency={result.latency_ms}ms tokens={result.usage.total_tokens}"
        ))
