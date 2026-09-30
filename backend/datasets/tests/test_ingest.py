import io
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase, override_settings

from datasets import ingest
from datasets.ingest import IngestError, clean_sales, normalize_columns, read_table

REQ = ["date", "region", "product", "category", "units", "revenue", "cost",
       "customer", "salesperson"]


def make_df(n=100, **overrides):
    """A clean, already-normalised frame of raw (object) values."""
    data = {
        "date": ["2025-03-01"] * n, "region": ["East"] * n, "product": ["Widget"] * n,
        "category": ["Tools"] * n, "units": ["3"] * n, "revenue": ["30.50"] * n,
        "cost": ["20.00"] * n, "customer": ["Acme"] * n, "salesperson": ["Sam"] * n,
    }
    for key, value in overrides.items():
        data[key] = value if isinstance(value, list) else [value] * n
    return pd.DataFrame(data).astype(object)


def csv_bytes(df, sep=","):
    return df.to_csv(index=False, sep=sep).encode()


class ColumnTests(SimpleTestCase):
    def test_headers_are_normalised(self):
        raw = pd.DataFrame({" Date ": ["2025-01-01"], "Revenue ($)": ["1"], "Sales Person": ["x"]})
        with self.assertRaises(IngestError) as ctx:
            normalize_columns(raw)
        self.assertEqual(ctx.exception.code, "missing_columns")
        self.assertIn("date", ctx.exception.details["found"])
        self.assertIn("revenue", ctx.exception.details["found"])
        self.assertIn("sales_person", ctx.exception.details["found"])

    def test_missing_columns_are_listed(self):
        df = make_df(3).drop(columns=["cost", "region"])
        with self.assertRaises(IngestError) as ctx:
            normalize_columns(df)
        self.assertEqual(set(ctx.exception.details["missing"]), {"cost", "region"})

    def test_duplicate_columns_rejected(self):
        df = make_df(3)
        df.columns = ["date", "Region", "region"] + REQ[3:]
        with self.assertRaises(IngestError) as ctx:
            normalize_columns(df)
        self.assertEqual(ctx.exception.code, "duplicate_columns")

    def test_extras_kept_after_required_in_order(self):
        df = make_df(3)
        df.insert(0, "Discount Code", ["a", "b", "c"])
        out = normalize_columns(df)
        self.assertEqual(list(out.columns), REQ + ["discount_code"])

    def test_empty_unnamed_columns_dropped(self):
        df = make_df(3)
        df["Unnamed: 9"] = None
        self.assertEqual(list(normalize_columns(df).columns), REQ)


class NumberTests(SimpleTestCase):
    def test_currency_and_thousands_separators(self):
        df = make_df(4, revenue=["₹1,20,000.50", "$1,234.50", " 99 ", "12,345"])
        out, _ = clean_sales(df)
        self.assertEqual(list(out["revenue"]), [120000.50, 1234.50, 99.0, 12345.0])

    def test_european_decimal_comma_is_rejected_not_misread(self):
        df = make_df(100)
        df.loc[0, "revenue"] = "1.234,56"
        out, report = clean_sales(df)
        self.assertEqual(report["rows_dropped"], 1)
        self.assertEqual(report["dropped_examples"][0]["value"], "1.234,56")
        self.assertNotIn(1, list(out["_source_row"]))

    def test_inf_and_nan_strings_are_not_data(self):
        df = make_df(100)
        df.loc[0, "cost"], df.loc[1, "cost"] = "inf", "nan"
        _, report = clean_sales(df)
        self.assertEqual(report["dropped_by_column"], {"cost": 2})

    def test_integral_units_become_int_fractional_stay_float(self):
        out, _ = clean_sales(make_df(5, units=["1", "2", "3", "4", "5"]))
        self.assertEqual(str(out["units"].dtype), "int64")
        out, _ = clean_sales(make_df(2, units=["1.5", "2"]))
        self.assertEqual(str(out["units"].dtype), "float64")

    def test_negative_values_warn_but_are_kept(self):
        out, report = clean_sales(make_df(3, revenue=["-5", "10", "10"]))
        self.assertEqual(len(out), 3)
        self.assertTrue(any("negative revenue" in w for w in report["warnings"]))


class DateTests(SimpleTestCase):
    def dates(self, values, **kw):
        n = len(values)
        out, report = clean_sales(make_df(n, date=values), **kw)
        return [d.date().isoformat() for d in out["date"]], report

    def test_iso_dates(self):
        got, report = self.dates(["2025-01-05", "2025-12-25T10:30:00"])
        self.assertEqual(got, ["2025-01-05", "2025-12-25"])
        self.assertEqual(report["date_format"], "iso")

    def test_day_first_detected_from_unambiguous_value(self):
        got, report = self.dates(["05/01/2025", "25/12/2025"])
        self.assertEqual(got, ["2025-01-05", "2025-12-25"])  # 05/01 is 5 Jan, not 1 May
        self.assertEqual(report["date_format"], "day-first (detected)")

    def test_month_first_detected_from_unambiguous_value(self):
        got, _ = self.dates(["05/01/2025", "12/25/2025"])
        self.assertEqual(got, ["2025-05-01", "2025-12-25"])

    def test_mixed_formats_rejected(self):
        with self.assertRaises(IngestError) as ctx:
            self.dates(["25/12/2025", "12/25/2025"])
        self.assertEqual(ctx.exception.code, "inconsistent_dates")

    def test_fully_ambiguous_uses_flag_and_warns(self):
        got, report = self.dates(["05/06/2025"], dayfirst=True)
        self.assertEqual(got, ["2025-06-05"])
        got, report = self.dates(["05/06/2025"], dayfirst=False)
        self.assertEqual(got, ["2025-05-06"])
        self.assertTrue(any("ambiguous" in w for w in report["warnings"]))

    def test_excel_serial_number_is_invalid_not_1970(self):
        df = make_df(100)
        df.loc[0, "date"] = 45292  # a bare number in a date column
        out, report = clean_sales(df)
        self.assertEqual(report["dropped_by_column"], {"date": 1})
        self.assertTrue((out["date"].dt.year == 2025).all())


class CleaningTests(SimpleTestCase):
    def test_bad_rows_dropped_and_traceable(self):
        df = make_df(100)
        df.loc[6, "revenue"] = "abc"      # source row 7
        df.loc[41, "date"] = "not a date"  # source row 42
        out, report = clean_sales(df)
        self.assertEqual((report["rows_read"], report["rows_kept"], report["rows_dropped"]),
                         (100, 98, 2))
        self.assertEqual({e["source_row"] for e in report["dropped_examples"]}, {7, 42})
        self.assertNotIn(7, list(out["_source_row"]))
        self.assertEqual(out["_source_row"].iloc[6], 8)  # numbering skips dropped rows

    def test_too_many_bad_rows_rejected_with_examples(self):
        df = make_df(100)
        df.loc[:9, "revenue"] = "oops"
        with self.assertRaises(IngestError) as ctx:
            clean_sales(df)
        self.assertEqual(ctx.exception.code, "too_many_invalid_rows")
        self.assertEqual(ctx.exception.details["dropped_by_column"], {"revenue": 10})
        self.assertEqual(len(ctx.exception.details["examples"]), 5)

    def test_date_failures_hint_at_dayfirst(self):
        df = make_df(10, date=["garbage"] * 10)
        with self.assertRaises(IngestError) as ctx:
            clean_sales(df)
        self.assertIn("dayfirst", ctx.exception.message)

    def test_blank_dimension_becomes_unknown_and_is_counted(self):
        df = make_df(4, region=["East", "", None, "  "])
        out, report = clean_sales(df)
        self.assertEqual(list(out["region"]), ["East", "Unknown", "Unknown", "Unknown"])
        self.assertEqual(report["filled_unknown"], {"region": 3})

    def test_na_is_a_valid_region_not_missing(self):
        csv = csv_bytes(make_df(3, region=["NA", "EU", "APAC"]))
        out, report = clean_sales(normalize_columns(read_table(csv, "csv")))
        self.assertEqual(list(out["region"]), ["NA", "EU", "APAC"])
        self.assertEqual(report["filled_unknown"], {})

    def test_excel_float_codes_lose_the_dot_zero(self):
        out, _ = clean_sales(make_df(2, product=[1001.0, "ABC"]))
        self.assertEqual(list(out["product"]), ["1001", "ABC"])

    def test_extra_columns_typed(self):
        df = make_df(3)
        df["discount"] = ["1.5", "2", "3"]
        df["channel"] = ["web", "store", "web"]
        out, report = clean_sales(df)
        self.assertEqual(report["extra_columns"], {"discount": "measure", "channel": "dimension"})
        self.assertEqual(out["discount"].dtype, "float64")

    def test_duplicates_warn_but_are_kept(self):
        out, report = clean_sales(make_df(5))
        self.assertEqual(len(out), 5)
        self.assertTrue(any("exact duplicates" in w for w in report["warnings"]))


class ReadTableTests(SimpleTestCase):
    def test_semicolon_delimiter_detected(self):
        df = read_table(csv_bytes(make_df(3), sep=";"), "csv")
        self.assertEqual(list(df.columns), REQ)

    def test_utf8_bom_and_cp1252(self):
        raw = csv_bytes(make_df(2, customer=["Café", "Bar"]))
        self.assertEqual(read_table(b"\xef\xbb\xbf" + raw, "csv")["date"].iloc[0], "2025-03-01")
        latin = raw.decode().replace("Café", "Caf\xe9").encode("cp1252")
        self.assertEqual(read_table(latin, "csv")["customer"].iloc[0], "Café")

    def test_binary_masquerading_as_csv_rejected(self):
        with self.assertRaises(IngestError) as ctx:
            read_table(b"PK\x03\x04\x00\x00" + b"\x00" * 50, "csv")
        self.assertEqual(ctx.exception.code, "not_a_csv")

    def test_header_only_file_is_empty(self):
        with self.assertRaises(IngestError) as ctx:
            read_table(",".join(REQ).encode() + b"\n", "csv")
        self.assertEqual(ctx.exception.code, "empty_file")

    def test_text_renamed_to_xlsx_rejected(self):
        with self.assertRaises(IngestError) as ctx:
            read_table(b"date,region\n1,2\n", "xlsx")
        self.assertEqual(ctx.exception.code, "not_an_xlsx")

    @override_settings(MAX_ROWS=5)
    def test_row_cap(self):
        with self.assertRaises(IngestError) as ctx:
            read_table(csv_bytes(make_df(6)), "csv")
        self.assertEqual(ctx.exception.code, "too_many_rows")

    def _xlsx(self, df):
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue()

    def test_xlsx_with_real_dates_and_numbers(self):
        df = make_df(3)
        df["date"] = pd.to_datetime(["2025-01-05", "2025-02-10", "2025-03-15"])
        df["units"] = [1, 2, 3]
        df["revenue"] = [10.5, 20.25, 30.0]
        out, report = clean_sales(normalize_columns(read_table(self._xlsx(df), "xlsx")))
        self.assertEqual([d.date().isoformat() for d in out["date"]],
                         ["2025-01-05", "2025-02-10", "2025-03-15"])
        self.assertEqual(list(out["revenue"]), [10.5, 20.25, 30.0])
        self.assertEqual(report["date_format"], "iso")

    def test_xlsx_zip_bomb_guard(self):
        data = self._xlsx(make_df(3))
        with mock.patch.object(ingest, "MAX_XLSX_UNCOMPRESSED_BYTES", 10):
            with self.assertRaises(IngestError) as ctx:
                read_table(data, "xlsx")
        self.assertEqual(ctx.exception.code, "file_too_large")
