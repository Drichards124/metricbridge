"""The marketplace catalog's seed: small, deterministic, shaped for fan and chasm traps.

Items and reviews are two facts related only through sellers; the shipping fee is charged once per
order while items repeat per order. Some items name a seller that does not exist, and some buyers,
sellers and items have no region, tier or category.
"""

import random
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path

import duckdb
from storefront_seed import copy_rows

SEED = 20260915
ORPHAN_SELLER = 42

SCHEMA = """
CREATE SCHEMA marketplace;
CREATE TABLE marketplace.dim_sellers (seller_id INTEGER, tier VARCHAR);
CREATE TABLE marketplace.dim_buyers (buyer_id INTEGER, region VARCHAR);
CREATE TABLE marketplace.fct_orders (
    order_id INTEGER, buyer_id INTEGER, ordered_date DATE, order_status VARCHAR,
    shipping_fee DECIMAL(10, 2)
);
CREATE TABLE marketplace.fct_order_items (
    order_item_id INTEGER, order_id INTEGER, seller_id INTEGER, buyer_id INTEGER, sold_date DATE,
    item_category VARCHAR, price DECIMAL(10, 2)
);
CREATE TABLE marketplace.fct_reviews (
    review_id INTEGER, seller_id INTEGER, reviewed_date DATE, stars INTEGER
);
"""

_directory = tempfile.TemporaryDirectory(prefix="metricbridge-marketplace-")


@cache
def marketplace_database() -> Path:
    # Not marketplace.duckdb: the catalog would share its name with the schema.
    path = Path(_directory.name) / "corpus.duckdb"
    rng = random.Random(SEED)
    sellers = [(seller, rng.choice(["gold", "silver", None])) for seller in range(1, 9)]
    buyers = [(buyer, rng.choice(["EU", "US", None])) for buyer in range(1, 21)]
    orders, items = [], []
    for order in range(1, 201):
        buyer = rng.choice([*range(1, 21), None])
        day = date(2026, 1, 1) + timedelta(rng.randrange(273))
        status = rng.choice(["paid", "refunded", "cancelled"])
        orders.append((order, buyer, day, status, Decimal(rng.randint(0, 1500)) / 100))
        for _ in range(rng.randint(1, 3)):
            seller = rng.choices([rng.randint(1, 8), ORPHAN_SELLER], weights=[97, 3])[0]
            category = rng.choice(["art", "books", "toys", None])
            price = Decimal(rng.randint(100, 20000)) / 100
            items.append((len(items) + 1, order, seller, buyer, day, category, price))
    reviews = [
        (
            review,
            rng.randint(1, 8),
            date(2026, 1, 1) + timedelta(rng.randrange(273)),
            rng.randint(1, 5),
        )
        for review in range(1, 151)
    ]
    with duckdb.connect(str(path)) as connection:
        connection.execute(SCHEMA)
        for table, rows in [
            ("marketplace.dim_sellers", sellers),
            ("marketplace.dim_buyers", buyers),
            ("marketplace.fct_orders", orders),
            ("marketplace.fct_order_items", items),
            ("marketplace.fct_reviews", reviews),
        ]:
            copy_rows(connection, table, rows)
    return path
