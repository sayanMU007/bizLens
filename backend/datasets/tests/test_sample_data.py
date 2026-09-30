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

    def test_columns(self):
        self.assertEqual(list(self.df.columns[:9]), COLUMNS)

    def test_december_drop_is_the_designed_story(self):
        # Compare average revenue per day: months have 28-31 days, which alone moves totals ~10%.
        monthly = self.df.groupby("month")["revenue"].sum()
        days = pd.Series({m: calendar.monthrange(2025, int(m[5:]))[1] for m in monthly.index})
        daily = (monthly / days).pct_change()
        self.assertLess(daily["2025-12"], -0.10)
        # every earlier move is small, so December is the only real drop in the data
        self.assertLess(daily.loc["2025-02":"2025-11"].abs().max(), 0.10)

    def test_east_is_the_main_driver_and_others_partly_offset(self):
        by = self.df.pivot_table(index="region", columns="month", values="revenue", aggfunc="sum")
        delta = by["2025-12"] - by["2025-11"]
        self.assertEqual(delta.idxmin(), "East")
        self.assertLess(delta["East"], -100_000)
        self.assertGreater(delta.drop("East").sum(), 0)  # the others grew a little in aggregate

    def test_big_customers_stop_in_december(self):
        dec_east = self.df[(self.df.month == "2025-12") & (self.df.region == "East")]
        self.assertNotIn("Northwind Traders", set(dec_east.customer))
        self.assertNotIn("Contoso Retail", set(dec_east.customer))
