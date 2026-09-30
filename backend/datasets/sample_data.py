"""Deterministic demo dataset with a built-in story.

Calendar 2025, four regions. In December the two biggest East-region customers stop
ordering and Aurora Headphones volume halves in East, so December revenue falls versus
November and the decline is concentrated in East. This is the answer BizLens should find.
"""
from __future__ import annotations

import datetime
import random

import pandas as pd

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
    day = datetime.date(2025, 1, 1)
    while day.year == 2025:
        december = day.month == 12
        for region, (customers, salespeople) in REGIONS.items():
            cust_weights = [4 if (region == "East" and c in BIG_EAST_CUSTOMERS) else 1
                            for c in customers]
            for _ in range(rng.randint(8, 12)):
                name, category, price, unit_cost, _w = rng.choices(PRODUCTS, weights)[0]
                customer = rng.choices(customers, cust_weights)[0]
                # Big-ticket items sell in small quantities; keeps monthly noise low so the
                # designed December drop is the only large movement in the data.
                units = rng.randint(1, 4) if price >= 300 else rng.randint(1, 12)
                if december and region == "East":
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
