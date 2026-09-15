"""The storefront catalog's conformance seed: deterministic, and large enough to reach the edges.

Nothing here is worked out by hand — each case's reference SQL is the oracle. What the seed
guarantees is that the edges exist:

- August 2025 has no orders at all, and March 2026 no subscription revenue;
- order lines with no customer, and with customer or product keys that name nothing;
- orders with no channel, customers with no region, products with no category;
- refunds as negative amounts;
- inventory days skipped per product and warehouse, so the last snapshot differs between products;
- shipments loaded up to the declared three-day partition lag after they shipped;
- `region` on both customers and products, so a bare `region` is ambiguous;
- weeks and quarters that straddle a year boundary.
"""

import csv
import random
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path

import duckdb
from storefront_data import SCHEMA

SEED = 20260914
REGIONS = ["EMEA", "AMER", "APAC"]
ORPHAN_CUSTOMER = 99
ORPHAN_PRODUCT = 77

_directory = tempfile.TemporaryDirectory(prefix="metricbridge-conformance-")


def _customers(rng: random.Random) -> list[tuple]:
    return [
        (
            customer,
            rng.choice([*REGIONS, None]),
            rng.choice(["enterprise", "strategic", "smb", None]),
            date(2024, 1, 1) + timedelta(rng.randrange(900)),
        )
        for customer in range(1, 41)
    ]


def _products(rng: random.Random) -> list[tuple]:
    return [
        (product, rng.choice(["books", "games", "tools", None]), rng.choice(REGIONS))
        for product in range(1, 26)
    ]


def _customer(rng: random.Random) -> int | None:
    return rng.choices([rng.randint(1, 40), None, ORPHAN_CUSTOMER], weights=[94, 3, 3])[0]


def _order_lines(rng: random.Random) -> list[tuple]:
    lines = []
    for order in range(1, 901):
        day = date(2025, 1, 1) + timedelta(rng.randrange(638))  # up to 2026-09-30
        if (day.year, day.month) == (2025, 8):
            continue
        customer, channel = _customer(rng), rng.choice(["web", "store", "app", None])
        for _ in range(rng.randint(1, 4)):
            product = rng.choices([rng.randint(1, 25), ORPHAN_PRODUCT], weights=[98, 2])[0]
            amount = Decimal(rng.randint(-2000, 50000)) / 100
            created = day - timedelta(rng.randint(0, 2))
            lines.append((len(lines) + 1, order, customer, product, day, created, channel, amount))
    return lines


def _subscription_revenue(rng: random.Random) -> list[tuple]:
    rows = []
    for subscription in range(1, 16):
        customer = _customer(rng)
        start = date(2024, 1, 1) + timedelta(rng.randrange(500))
        end = min(start + timedelta(rng.randrange(120, 800)), date(2026, 9, 30))
        day = start
        while day <= end:
            if (day.year, day.month) != (2026, 3) and rng.random() < 0.5:
                rows.append((day, rng.randint(1, 20), subscription, customer))
            day += timedelta(1)
    return rows


def _inventory(rng: random.Random) -> list[tuple]:
    rows = []
    day = date(2026, 1, 1)
    while day <= date(2026, 9, 30):
        for product in [*range(1, 26), ORPHAN_PRODUCT]:
            for warehouse in ["north", "south", None]:
                if rng.random() < 0.35:
                    rows.append((product, day, warehouse, rng.randint(0, 500)))
        day += timedelta(1)
    return rows


def _shipments(rng: random.Random) -> list[tuple]:
    rows = []
    for shipment in range(1, 701):
        shipped = date(2025, 6, 1) + timedelta(rng.randrange(487))  # up to 2026-09-30
        loaded = shipped + timedelta(rng.randint(0, 3))
        carrier = rng.choice(["dhl", "ups", "fedex", None])
        rows.append((shipment, _customer(rng), loaded, shipped, carrier))
    return rows


@cache
def storefront_corpus_database() -> Path:
    # Not storefront.duckdb: DuckDB names the catalog after the file, which would make every
    # `storefront.<table>` reference ambiguous with the schema.
    path = Path(_directory.name) / "corpus.duckdb"
    rng = random.Random(SEED)
    tables = [
        ("storefront.dim_customers", _customers(rng)),
        ("storefront.dim_products", _products(rng)),
        ("storefront.fct_order_lines", _order_lines(rng)),
        ("billing.fct_subscription_revenue_daily", _subscription_revenue(rng)),
        ("warehouse.fct_inventory_daily", _inventory(rng)),
        ("logistics.fct_shipments", _shipments(rng)),
    ]
    with duckdb.connect(str(path)) as connection:
        connection.execute(SCHEMA)
        for table, rows in tables:
            copy_rows(connection, table, rows)
    return path


def copy_rows(connection: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> None:
    """COPY from a file: binding parameters took 4 s for the storefront seed. No seeded value
    contains a tab, and \\N is never a value, so NULL stays distinct from an empty string."""
    source = Path(_directory.name) / f"{table}.tsv"
    with source.open("w", newline="") as file:
        csv.writer(file, delimiter="\t", lineterminator="\n").writerows(
            [r"\N" if value is None else value for value in row] for row in rows
        )
    connection.execute(
        f"COPY {table} FROM '{source}' (DELIMITER '\t', HEADER false, NULLSTR '\\N')"
    )
