"""Deterministic demo dataset with a built-in story.

Jan 2025 - Sep 2026, four regions, a gentle upward trend. In September 2026 the two
biggest East-region customers stop ordering and Aurora Headphones volume halves in East,
so September revenue falls versus August and the decline is concentrated in East.
That is the answer BizLens should find for "Why did revenue decrease last month?".
"""
from __future__ import annotations

import datetime
import random

import pandas as pd

START = datetime.date(2025, 1, 1)
END = datetime.date(2026, 9, 30)
STORY_YEAR, STORY_MONTH = 2026, 9

# name, category, unit price, unit cost, sales weight
PRODUCTS = [
    ("Aurora Headphones", "Audio", 120.0, 70.0, 3),
    ("Pulse Speaker", "Audio", 80.0, 45.0, 3),
    ("Nimbus Tablet", "Computing", 300.0, 210.0, 2),
    ("Vertex Laptop", "Computing", 900.0, 680.0, 1),
    ("Halo Smartwatch", "Wearables", 200.0, 120.0, 2),
    ("Loop Fitness Band", "Wearables", 60.0, 30.0, 3),
]
BIG_EAST_CUSTOMERS = ("Northwind Traders", "Contoso Retail")
REGIONS = {
    "East": (["Northwind Traders", "Contoso Retail", "Fabrikam Stores", "Litware Retail",
              "Proseware Inc", "Adventure Works", "Tailspin Toys", "Wingtip Shops"],
             ["Asha Rao", "Ben Carter"]),
    "West": (["Coho Vineyard", "Lucerne Publishing", "Margie's Travel", "Wide World Importers",
              "Alpine Ski House", "Blue Yonder", "City Power", "Datum Corp"],
             ["Chloe Kim", "Dev Patel"]),
    "North": (["Fourth Coffee", "Graphic Design Inst", "Humongous Insurance", "Trey Research",
               "VanArsdel Ltd", "Woodgrove Bank", "Relecloud", "School of Fine Art"],
              ["Elena Petrov", "Farid Khan"]),
    "South": (["Northwind South", "Contoso South", "Bellows College", "Consolidated Messenger",
               "First Up Consultants", "Southridge Video", "Lamna Healthcare", "Munson's Pickles"],
              ["Grace Lee", "Hiro Tanaka"]),
}
COLUMNS = ["date", "region", "product", "category", "units", "revenue", "cost",
           "customer", "salesperson"]


def generate_sales(seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    weights = [p[4] for p in PRODUCTS]
    rows = []
    day = START
    while day <= END:
        month_index = (day.year - START.year) * 12 + day.month - START.month
        story_month = (day.year, day.month) == (STORY_YEAR, STORY_MONTH)
        # Gentle growth: the chance of one extra transaction per region-day rises over time.
        growth = min(month_index * 0.05, 1.0)
        for region, (customers, salespeople) in REGIONS.items():
            cust_weights = [4 if (region == "East" and c in BIG_EAST_CUSTOMERS) else 1
                            for c in customers]
            n = rng.randint(8, 12) + (1 if rng.random() < growth else 0)
            for _ in range(n):
                name, category, price, unit_cost, _w = rng.choices(PRODUCTS, weights)[0]
                customer = rng.choices(customers, cust_weights)[0]
                # Big-ticket items sell in small quantities; keeps monthly noise low so the
                # designed drop is the only large movement in the data.
                units = rng.randint(1, 4) if price >= 300 else rng.randint(1, 12)
                if story_month and region == "East":
                    if customer in BIG_EAST_CUSTOMERS:
                        continue  # these customers stopped ordering
                    if name == "Aurora Headphones":
                        units = max(1, units // 2)
                rows.append({
                    "date": day.isoformat(),
                    "region": region,
                    "product": name,
                    "category": category,
                    "units": units,
                    "revenue": round(units * price * (1 + rng.uniform(-0.05, 0.05)), 2),
                    "cost": round(units * unit_cost * (1 + rng.uniform(-0.03, 0.03)), 2),
                    "customer": customer,
                    "salesperson": rng.choice(salespeople),
                })
        day += datetime.timedelta(days=1)
    return pd.DataFrame(rows, columns=COLUMNS)
