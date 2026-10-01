import json

from django.core.management.base import BaseCommand, CommandError

from ai.provider import LLMError
from datasets.models import Dataset

from ask.pipeline import AskError, ask


class Command(BaseCommand):
    help = "Ask a question about a dataset from the terminal (makes live LLM calls)."

    def add_arguments(self, parser):
        parser.add_argument("dataset_id")
        parser.add_argument("question")
        parser.add_argument("--json", action="store_true", help="print the full response")

    def handle(self, *args, **opts):
        try:
            dataset = Dataset.objects.prefetch_related("columns").get(pk=opts["dataset_id"])
        except (Dataset.DoesNotExist, ValueError):
            raise CommandError("No such dataset.")
        try:
            response = ask(dataset, opts["question"])
        except AskError as exc:
            for t in exc.trace:
                self.stderr.write(f"  [{t.status}] {t.stage}: {t.detail or ''}")
            raise CommandError(f"{exc.code}: {exc.message}")
        except LLMError as exc:
            raise CommandError(f"{exc.__class__.__name__}: {exc}")
        if opts["json"]:
            self.stdout.write(json.dumps(response.model_dump(mode="json"), indent=2))
            return
        self.stdout.write(f"route: {response.route}   status: {response.status}")
        self.stdout.write(f"plan reasoning: {response.plan.reasoning}")
        if response.analysis:
            self.stdout.write(f"\n{response.analysis.headline}")
            for q in response.analysis.queries:
                self.stdout.write(f"  {q.id}: {q.purpose} ({q.row_count} rows, {q.duration_ms} ms)")
        else:
            self.stdout.write(f"\n{response.message}")
        self.stdout.write("\ntrace:")
        for t in response.trace:
            self.stdout.write(f"  [{t.status}] {t.stage} {t.model or ''} {t.detail or ''}")
