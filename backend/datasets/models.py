import uuid

from django.db import models


class Dataset(models.Model):
    """One uploaded, validated, immutable dataset.

    The business data itself lives in Parquet (see storage.py), never in Postgres.
    `sha256` fingerprints the original upload so evidence can prove which data it used.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    original_filename = models.CharField(max_length=255)
    file_format = models.CharField(max_length=8)  # "csv" | "xlsx"
    file_size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    row_count = models.BigIntegerField()
    # Column stats, date range, and the validation report (what was dropped/filled/warned).
    profile = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


class DatasetColumn(models.Model):
    """Typed schema of a dataset. Phase 4 validates LLM plans/SQL against these rows."""

    dataset = models.ForeignKey(Dataset, related_name="columns", on_delete=models.CASCADE)
    name = models.CharField(max_length=128)
    position = models.PositiveSmallIntegerField()
    dtype = models.CharField(max_length=32)  # DuckDB type: DATE, BIGINT, DOUBLE, VARCHAR
    role = models.CharField(max_length=16)  # time | dimension | measure
    is_required = models.BooleanField(default=False)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["dataset", "name"], name="uniq_dataset_column"),
        ]
