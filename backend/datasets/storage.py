"""On-disk layout: DATA_DIR/datasets/<uuid>/{original.<ext>, data.parquet}.

Paths are built only from a server-generated UUID and a fixed file name, never from
user input, so a hostile filename cannot escape the directory. Phase 9 can swap this
module for object storage without touching callers.
"""
import shutil
from pathlib import Path

from django.conf import settings


def dataset_dir(dataset_id) -> Path:
    return Path(settings.DATA_DIR).resolve() / "datasets" / str(dataset_id)


def parquet_path(dataset_id) -> Path:
    return dataset_dir(dataset_id) / "data.parquet"


def raw_path(dataset_id, file_format: str) -> Path:
    return dataset_dir(dataset_id) / f"original.{file_format}"


def delete_dataset_files(dataset_id) -> None:
    shutil.rmtree(dataset_dir(dataset_id), ignore_errors=True)
