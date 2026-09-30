"""Upload -> validate -> clean -> Parquet -> profile.

Principles:
  * Nothing is silently changed. Every dropped row, filled value, and assumption is
    recorded in the validation report stored on the dataset (and later cited as evidence
    assumptions).
  * Every kept row carries `_source_row`: its 1-based position among the file's data rows,
    so any number can be traced back to a line in the user's original file.
  * Ambiguous input is rejected or flagged, never guessed (see date handling).
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from django.conf import settings
from django.db import transaction

from . import duck, storage
from .models import Dataset, DatasetColumn
from .profile import profile_dataset

logger = logging.getLogger("bizlens.datasets")

MAX_XLSX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # zip-bomb guard
UNKNOWN = "Unknown"


class IngestError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    kind: str  # "date" | "number" | "text"
    role: str  # "time" | "dimension" | "measure"


SALES_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("date", "date", "time"),
    ColumnSpec("region", "text", "dimension"),
    ColumnSpec("product", "text", "dimension"),
    ColumnSpec("category", "text", "dimension"),
    ColumnSpec("units", "number", "measure"),
    ColumnSpec("revenue", "number", "measure"),
    ColumnSpec("cost", "number", "measure"),
    ColumnSpec("customer", "text", "dimension"),
    ColumnSpec("salesperson", "text", "dimension"),
)
_SPEC = {c.name: c for c in SALES_COLUMNS}


# --------------------------------------------------------------------------- reading
def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _check_xlsx_archive(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if sum(i.file_size for i in zf.infolist()) > MAX_XLSX_UNCOMPRESSED_BYTES:
            raise IngestError("file_too_large", "The spreadsheet expands to an unsafe size.")


def read_table(data: bytes, file_format: str) -> pd.DataFrame:
    """Read every cell as text/raw objects; typing happens later, with reporting."""
    try:
        if file_format == "csv":
            if b"\x00" in data[:8192]:
                raise IngestError("not_a_csv", "This does not look like a text CSV file.")
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1252", errors="replace")
            df = pd.read_csv(
                io.StringIO(text),
                sep=_sniff_delimiter(text[:8192]),
                dtype=str,
                keep_default_na=False,  # "NA" can be a real region name (North America)
                na_values=[""],
            )
        else:
            if not data.startswith(b"PK"):
                raise IngestError("not_an_xlsx", "This does not look like an .xlsx file.")
            _check_xlsx_archive(data)
            df = pd.read_excel(
                io.BytesIO(data), sheet_name=0, dtype=object,
                keep_default_na=False, na_values=[""],
            )
    except IngestError:
        raise
    except Exception as exc:  # parser boundary: any failure means "unreadable"
        logger.warning("unreadable upload: %s", exc.__class__.__name__)
        raise IngestError("unreadable_file", "The file could not be read as a table.") from exc

    if len(df) == 0:
        raise IngestError("empty_file", "The file has a header but no data rows.")
    if len(df) > settings.MAX_ROWS:
        raise IngestError("too_many_rows", f"The file has more than {settings.MAX_ROWS:,} rows.")
    return df.astype(object)


# --------------------------------------------------------------------------- schema
def normalize_name(name: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = [c for c in df.columns
            if not (str(c).lower().startswith("unnamed:") and df[c].isna().all())]
    df = df[keep]
    names = [normalize_name(c) for c in df.columns]
    if any(not n for n in names):
        raise IngestError("invalid_header", "Every column needs a header name.")
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise IngestError("duplicate_columns",
                          f"Duplicate column names after normalisation: {', '.join(dupes)}.",
                          {"columns": dupes})
    df = df.copy()
    df.columns = names
    missing = [c.name for c in SALES_COLUMNS if c.name not in names]
    if missing:
        raise IngestError(
            "missing_columns",
            f"Missing required column(s): {', '.join(missing)}.",
            {"missing": missing, "found": names,
             "required": [c.name for c in SALES_COLUMNS]},
        )
    extras = [n for n in names if n not in _SPEC]
    return df[[c.name for c in SALES_COLUMNS] + extras]


# --------------------------------------------------------------------------- typing
_WESTERN = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")  # 1,234,567.89
_INDIAN = re.compile(r"^-?\d{1,2}(,\d{2})+,\d{3}(\.\d+)?$")  # 12,34,567.89
_STRIP = str.maketrans("", "", "₹$€£ \u00a0")


def _numeric_token(v: Any) -> Any:
    if isinstance(v, str):
        t = v.strip().translate(_STRIP)
        # Only remove commas when they are clearly thousands separators. "1.234,56"
        # (European decimal comma) is left alone and rejected rather than misread.
        if "," in t and (_WESTERN.match(t) or _INDIAN.match(t)):
            t = t.replace(",", "")
        return t
    return v


def _to_numeric(s: pd.Series) -> pd.Series:
    num = pd.to_numeric(s.map(_numeric_token), errors="coerce").astype("float64")
    return num.where(np.isfinite(num))  # "inf"/"nan" strings are not data


def _to_text(v: Any) -> str | None:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # Excel stores product code 1001 as 1001.0
    t = str(v).strip()
    return t or None


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")
_SLASH = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}\b")


def _decide_dayfirst(strings: list[str], requested: bool) -> tuple[bool, str]:
    """Infer day-first vs month-first from unambiguous values; never guess silently."""
    day_evidence = month_evidence = has_pattern = False
    for v in strings:
        m = _SLASH.match(v)
        if not m:
            continue
        has_pattern = True
        a, b = int(m[1]), int(m[2])
        day_evidence |= a > 12
        month_evidence |= b > 12
    if day_evidence and month_evidence:
        raise IngestError(
            "inconsistent_dates",
            "The date column mixes day/month/year and month/day/year formats. "
            "Please make them consistent (ISO YYYY-MM-DD is safest) and re-upload.",
        )
    if day_evidence:
        return True, "day-first (detected)"
    if month_evidence:
        return False, "month-first (detected)"
    if has_pattern:
        return requested, f"ambiguous, assumed {'day-first' if requested else 'month-first'}"
    return requested, "unambiguous"


def _parse_dates(s: pd.Series, dayfirst: bool) -> tuple[pd.Series, str]:
    strings = [v.strip() for v in s.dropna() if isinstance(v, str)]
    if all(_ISO.match(v) for v in strings):
        fmt_note, use_dayfirst = "iso", False
    else:
        use_dayfirst, fmt_note = _decide_dayfirst(strings, dayfirst)
    # A bare number in a date column (e.g. an Excel serial 45292) would be read as
    # nanoseconds since 1970 - silently wrong - so treat it as invalid instead.
    safe = s.map(lambda v: pd.NaT if isinstance(v, (int, float)) and not pd.isna(v) else v)
    try:
        parsed = pd.to_datetime(safe, errors="coerce", format="mixed", dayfirst=use_dayfirst)
    except (ValueError, TypeError) as exc:
        raise IngestError("invalid_dates", "The date column could not be interpreted.") from exc
    return parsed, fmt_note


# --------------------------------------------------------------------------- cleaning
def clean_sales(df: pd.DataFrame, dayfirst: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = df.reset_index(drop=True)
    n_read = len(df)
    warnings: list[str] = []

    parsed: dict[str, pd.Series] = {}
    parsed["date"], date_format = _parse_dates(df["date"], dayfirst)
    for name in ("units", "revenue", "cost"):
        parsed[name] = _to_numeric(df[name])

    invalid = {name: series.isna() for name, series in parsed.items()}
    drop_mask = pd.Series(False, index=df.index)
    for m in invalid.values():
        drop_mask |= m
    dropped_by_column = {n: int(m.sum()) for n, m in invalid.items() if m.sum()}

    examples = []
    for idx in df.index[drop_mask][:5]:
        col = next(n for n in invalid if invalid[n][idx])
        raw = df.at[idx, col]
        examples.append({"source_row": int(idx) + 1, "column": col,
                         "value": None if pd.isna(raw) else str(raw)[:50]})

    n_dropped = int(drop_mask.sum())
    if n_dropped / n_read > settings.MAX_INVALID_ROW_FRACTION:
        hint = ""
        if dropped_by_column.get("date"):
            hint = " If your dates are day/month/year, re-upload with dayfirst=true."
        raise IngestError(
            "too_many_invalid_rows",
            f"{n_dropped:,} of {n_read:,} rows have missing or unparseable "
            f"date/units/revenue/cost values (limit {settings.MAX_INVALID_ROW_FRACTION:.0%}).{hint}",
            {"dropped_by_column": dropped_by_column, "examples": examples},
        )

    keep = ~drop_mask
    out = pd.DataFrame({"_source_row": np.arange(1, n_read + 1, dtype="int64")})[keep]
    out = out.reset_index(drop=True)
    out["date"] = parsed["date"][keep].reset_index(drop=True)

    filled: dict[str, int] = {}
    for spec in SALES_COLUMNS:  # one pass, in spec order, so the stored schema matches the spec
        if spec.kind == "date":
            continue  # already added above
        if spec.kind == "text":
            col = df[spec.name][keep].map(_to_text).reset_index(drop=True)
            blanks = int(col.isna().sum())
            if blanks:
                filled[spec.name] = blanks
            out[spec.name] = col.fillna(UNKNOWN)
        else:
            out[spec.name] = parsed[spec.name][keep].reset_index(drop=True)
    if (out["units"] % 1 == 0).all():
        out["units"] = out["units"].astype("int64")

    # Extra (non-required) columns are kept: numeric if every value parses, else text.
    extra_types: dict[str, str] = {}
    for name in df.columns:
        if name in _SPEC:
            continue
        raw = df[name][keep].reset_index(drop=True)
        num = _to_numeric(raw)
        present = raw.notna()
        if present.any() and num[present].notna().all():
            out[name], extra_types[name] = num, "measure"
        else:
            out[name], extra_types[name] = raw.map(_to_text), "dimension"

    if date_format.startswith("ambiguous"):
        warnings.append(
            f"Dates like 05/06/2025 are ambiguous; they were read as {date_format.split(', ')[1]}. "
            "If that is wrong, re-upload with dayfirst set correctly."
        )
    for name in ("units", "revenue"):
        neg = int((out[name] < 0).sum())
        if neg:
            warnings.append(f"{neg:,} row(s) have negative {name} (returns/credits?). Kept as-is.")
    dupes = int(out.drop(columns="_source_row").duplicated().sum())
    if dupes:
        warnings.append(f"{dupes:,} row(s) are exact duplicates of another row. Kept as-is.")

    report = {
        "rows_read": n_read,
        "rows_kept": int(len(out)),
        "rows_dropped": n_dropped,
        "dropped_by_column": dropped_by_column,
        "dropped_examples": examples,
        "filled_unknown": filled,
        "date_format": date_format,
        "extra_columns": extra_types,
        "warnings": warnings,
    }
    return out, report


# --------------------------------------------------------------------------- persistence
def write_parquet(clean: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    select = ", ".join(
        f"CAST({duck.qident(c)} AS DATE) AS {duck.qident(c)}" if c == "date" else duck.qident(c)
        for c in clean.columns
    )
    con = duckdb.connect(":memory:")
    try:
        con.register("cleaned", clean)
        con.execute(
            f"COPY (SELECT {select} FROM cleaned ORDER BY _source_row) "
            f"TO {duck.qliteral(path)} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()


def ingest_upload(*, uploaded, name: str | None = None, dayfirst: bool = False) -> Dataset:
    filename = Path(uploaded.name or "").name
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in {"csv", "xlsx"}:
        raise IngestError("unsupported_format",
                          "Upload a .csv or .xlsx file (for .xls, save it as .xlsx first).")
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if uploaded.size > max_bytes:
        raise IngestError("file_too_large", f"The file exceeds {settings.MAX_UPLOAD_MB} MB.")

    data = uploaded.read()
    df = normalize_columns(read_table(data, ext))
    clean, report = clean_sales(df, dayfirst=dayfirst)

    dataset_id = uuid.uuid4()
    try:
        storage.dataset_dir(dataset_id).mkdir(parents=True, exist_ok=True)
        storage.raw_path(dataset_id, ext).write_bytes(data)
        write_parquet(clean, storage.parquet_path(dataset_id))

        dataset = Dataset(
            id=dataset_id,
            name=(name or filename.rsplit(".", 1)[0] or "dataset").strip()[:200],
            original_filename=filename[:255],
            file_format=ext,
            file_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            row_count=len(clean),
        )
        roles = {c.name: c.role for c in SALES_COLUMNS} | report["extra_columns"]
        with duck.open_dataset(dataset) as con:
            profile = profile_dataset(con, roles)
        profile["validation"] = report
        dataset.profile = profile

        required = {c.name for c in SALES_COLUMNS}
        with transaction.atomic():
            dataset.save()
            DatasetColumn.objects.bulk_create([
                DatasetColumn(dataset=dataset, name=c["name"], position=i, dtype=c["dtype"],
                              role=c["role"], is_required=c["name"] in required)
                for i, c in enumerate(profile["columns"])
            ])
    except Exception:
        storage.delete_dataset_files(dataset_id)
        raise
    return dataset
