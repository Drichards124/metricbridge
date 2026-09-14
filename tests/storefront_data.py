"""The storefront fixture's tables, seeded with a few rows whose answers are worked out by hand."""

import tempfile
from functools import cache
from pathlib import Path

import duckdb

SCHEMA = """
CREATE SCHEMA storefront;
CREATE TABLE storefront.fct_order_lines (
    order_line_id INTEGER, order_id INTEGER, customer_id INTEGER, product_id INTEGER,
    order_date DATE, created_at DATE, channel VARCHAR, amount DECIMAL(12, 2)
);
CREATE TABLE storefront.dim_customers (
    customer_id INTEGER, region VARCHAR, segment VARCHAR, created_at DATE
);
CREATE SCHEMA billing;
CREATE TABLE billing.fct_subscription_revenue_daily (
    revenue_date DATE, amount INTEGER, subscription_id INTEGER, customer_id INTEGER
);
-- Empty: no answer in the suite reads them, but the server verifies every declared table exists.
CREATE TABLE storefront.dim_products (product_id INTEGER, category VARCHAR, region VARCHAR);
CREATE SCHEMA warehouse;
CREATE TABLE warehouse.fct_inventory_daily (
    product_id INTEGER, snapshot_date DATE, warehouse VARCHAR, units INTEGER
);
CREATE SCHEMA logistics;
CREATE TABLE logistics.fct_shipments (
    shipment_id INTEGER, customer_id INTEGER, loaded_at DATE, shipped_date DATE, carrier VARCHAR
);
"""

ORDER_LINES = [
    # July: a two-line web order and a store order. August: nothing at all.
    (1, 1, 1, 1, "2026-07-03", "2026-07-03", "web", "100.50"),
    (2, 1, 1, 2, "2026-07-03", "2026-07-03", "web", "49.50"),
    (3, 2, 2, 1, "2026-07-20", "2026-07-20", "store", "80.00"),
    # The last day of Q3, with no customer and no channel.
    (4, 3, None, 1, "2026-09-30", "2026-09-30", None, "30.00"),
]
CUSTOMERS = [(1, "EMEA", "enterprise", "2026-01-01"), (2, "AMER", "smb", "2026-01-01")]
# £100 on the first of each month, January to September 2026.
SUBSCRIPTION_REVENUE = [(f"2026-{month:02d}-01", 100, 1, 1) for month in range(1, 10)]

_directory = tempfile.TemporaryDirectory(prefix="metricbridge-")


@cache
def storefront_database() -> Path:
    # Not storefront.duckdb: DuckDB names the catalog after the file, and a catalog called
    # `storefront` makes every `storefront.<table>` reference ambiguous with the schema.
    path = Path(_directory.name) / "seeded.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(SCHEMA)
        connection.executemany(
            "INSERT INTO storefront.fct_order_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ORDER_LINES
        )
        connection.executemany(
            "INSERT INTO storefront.dim_customers VALUES (?, ?, ?, ?)", CUSTOMERS
        )
        connection.executemany(
            "INSERT INTO billing.fct_subscription_revenue_daily VALUES (?, ?, ?, ?)",
            SUBSCRIPTION_REVENUE,
        )
    return path
