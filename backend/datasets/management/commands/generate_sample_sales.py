from pathlib import Path

from django.core.management.base import BaseCommand

from datasets.sample_data import generate_sales


class Command(BaseCommand):
    help = "Write a deterministic demo sales.csv (December revenue drop concentrated in East)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="sample_data/sales.csv")
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **opts):
        out = Path(opts["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        df = generate_sales(opts["seed"])
        df.to_csv(out, index=False)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(df):,} rows to {out}"))
