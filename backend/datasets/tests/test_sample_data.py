import calendar

import pandas as pd
from django.test import SimpleTestCase

from datasets.sample_data import COLUMNS, generate_sales


class SampleDataTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        df = generate_sales(42)
        df["month"] = df["date"].str[:7]
        cls.df = df

    def test_deterministic(self):
        self.assertTrue(generate_sales(42).equals(generate_sales(42)))
        self.assertFalse(generate_sales(42).equals(generate_sales(7)))

    def test_columns_and_date_range(self):
        self.assertEqual(list(self.df.columns[:9]), COLUMNS)
        self.assertEqual((self.df["date"].min(), self.df["date"].max()), ("2025-01-01", "2026-09-30"))
        self.assertEqual(self.df["month"].nunique(), 21)

    def test_september_2026_drop_is_the_designed_story(self):
        # Compare average revenue per day: months have 28-31 days, which alone moves totals ~10%.
        monthly = self.df.groupby("month")["revenue"].sum()
        days = pd.Series({m: calendar.monthrange(int(m[:4]), int(m[5:]))[1] for m in monthly.index})
        daily = (monthly / days).pct_change()
        self.assertLess(daily["2026-09"], -0.15)
        # every earlier move is small, so September 2026 is the only real drop in the data
        self.assertLess(daily.loc["2025-02":"2026-08"].abs().max(), 0.12)

    def test_east_is_the_main_driver(self):
        by = self.df.pivot_table(index="region", columns="month", values="revenue", aggfunc="sum")
        delta = by["2026-09"] - by["2026-08"]
        self.assertEqual(delta.idxmin(), "East")
        self.assertLess(delta["East"], -100_000)
        self.assertGreater(delta["East"] / delta.sum(), 0.6)  # most of the total decline

    def test_big_customers_stop_in_september_2026(self):
        east = self.df[self.df.region == "East"]
        sep = east[east.month == "2026-09"]
        self.assertNotIn("Northwind Traders", set(sep.customer))
        self.assertNotIn("Contoso Retail", set(sep.customer))
        aug = east[east.month == "2026-08"]
        self.assertIn("Northwind Traders", set(aug.customer))  # they were active until August
