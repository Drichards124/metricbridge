"""The compiler turns a resolved request into one parameterised statement.

The agent never writes SQL and never sees a database. Golden statements are checked in, so a change
that alters generated SQL shows up as a diff in review — which is how you find out you changed a
number before shipping it.
"""

import re
from datetime import date
from pathlib import Path

import pytest
from sqlglot import parse_one

from metricbridge.compiler import compile_query
from metricbridge.contract import QueryRequest, validate
from metricbridge.manifest import load_manifest

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"
Q3 = {"start_date": "2026-07-01", "end_date": "2026-09-30"}


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(STOREFRONT)


def compiled(manifest, dialect="duckdb", **overrides):
    request = QueryRequest(**{"metric": "revenue", "date_range": Q3, **overrides})
    return compile_query(manifest, validate(manifest, request), dialect=dialect)


def flat(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class TestGoldenStatements:
    def test_a_metric_with_no_cuts(self, manifest):
        assert flat(compiled(manifest).sql) == (
            "SELECT SUM(orders.amount) AS revenue "
            "FROM storefront.fct_order_lines AS orders "
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date "
            "LIMIT 100"
        )

    def test_a_metric_cut_by_time_and_a_joined_dimension(self, manifest):
        query = compiled(
            manifest,
            dimensions=["customer__region"],
            time_grain="month",
            order_by=[{"field": "revenue", "direction": "desc"}],
        )
        assert flat(query.sql) == (
            "SELECT DATE_TRUNC('MONTH', orders.order_date) AS period, "
            "customer.region AS customer__region, "
            "SUM(orders.amount) AS revenue, "
            "SUM(CASE WHEN orders.customer_id IS NULL THEN 1 ELSE 0 END) "
            "AS customer__null_key_rows "
            "FROM storefront.fct_order_lines AS orders "
            "LEFT JOIN storefront.dim_customers AS customer "
            "ON orders.customer_id = customer.customer_id "
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date "
            "GROUP BY DATE_TRUNC('MONTH', orders.order_date), customer.region "
            "ORDER BY revenue DESC "
            "LIMIT 100"
        )

    @pytest.mark.parametrize(
        ("dialect", "bucket", "placeholder"),
        [
            ("duckdb", "DATE_TRUNC('MONTH', orders.order_date)", "$start_date"),
            ("postgres", "DATE_TRUNC('MONTH', orders.order_date)", "%(start_date)s"),
            ("bigquery", "DATE_TRUNC(orders.order_date, MONTH)", "@start_date"),
            ("snowflake", "DATE_TRUNC('MONTH', orders.order_date)", ":start_date"),
        ],
    )
    def test_each_dialect_gets_its_own_spelling(self, manifest, dialect, bucket, placeholder):
        """The spike emitted one spelling everywhere, which is invalid on BigQuery."""
        sql = flat(compiled(manifest, dialect=dialect, time_grain="month").sql)
        assert bucket in sql
        assert placeholder in sql


class TestDateBounds:
    def test_the_range_is_half_open_so_the_last_day_is_not_dropped(self, manifest):
        """`BETWEEN '…-09-30'` excludes everything after midnight on a timestamp column."""
        query = compiled(manifest)
        assert query.parameters["start_date"] == date(2026, 7, 1)
        assert query.parameters["end_date"] == date(2026, 10, 1)
        assert ">= $start_date" in flat(query.sql)
        assert "< $end_date" in flat(query.sql)

    def test_a_late_arriving_table_bounds_both_columns(self, manifest):
        """Partitioned on load date, measured by ship date: bound the business date exactly, and
        the partition widened by the declared lag, or late rows vanish."""
        query = compiled(manifest, metric="shipments")
        sql = flat(query.sql)
        assert "shipments.shipped_date >= $start_date" in sql
        assert "shipments.shipped_date < $end_date" in sql
        assert "shipments.loaded_at >= $partition_start" in sql
        assert "shipments.loaded_at < $partition_end" in sql
        assert query.parameters["partition_start"] == date(2026, 6, 28)  # start - 3 days
        assert query.parameters["partition_end"] == date(2026, 10, 4)  # end + 1 + 3 days


class TestFilters:
    def test_a_dimension_filter_lands_in_where(self, manifest):
        query = compiled(manifest, filters=[{"field": "channel", "operator": "=", "value": "web"}])
        assert "orders.channel = $filter_0" in flat(query.sql)
        assert query.parameters["filter_0"] == "web"

    def test_a_metric_filter_lands_in_having(self, manifest):
        query = compiled(
            manifest,
            dimensions=["channel"],
            filters=[{"field": "revenue", "operator": ">", "value": 50000}],
        )
        sql = flat(query.sql)
        assert "HAVING SUM(orders.amount) > $filter_0" in sql
        assert query.parameters["filter_0"] == 50000

    def test_the_definition_travels_with_the_metric(self, manifest):
        """`web_revenue` is revenue where channel is web, whether or not the agent said so."""
        query = compiled(manifest, metric="web_revenue")
        assert "orders.channel = $metric_filter_0" in flat(query.sql)
        assert query.parameters["metric_filter_0"] == "web"

    def test_values_are_bound_never_interpolated(self, manifest):
        """A filter value is a string to the driver and nothing to the parser."""
        hostile = "O'Brien'; DROP TABLE orders; --"
        query = compiled(
            manifest, filters=[{"field": "channel", "operator": "=", "value": hostile}]
        )
        assert "DROP TABLE" not in query.sql
        assert query.parameters["filter_0"] == hostile


class TestAggregations:
    def test_a_distinct_count_is_recomputed_at_the_requested_grain(self, manifest):
        """Summing daily distinct counts into a month double-counts repeat customers."""
        sql = flat(compiled(manifest, metric="order_count", time_grain="month").sql)
        assert "COUNT(DISTINCT orders.order_id) AS order_count" in sql
        assert "SUM(" not in sql  # nothing is rolled up from a finer aggregate
        assert "GROUP BY DATE_TRUNC('MONTH', orders.order_date)" in sql

    def test_every_compiled_statement_is_a_single_parseable_select(self, manifest):
        for name in manifest.metrics:
            if manifest.metrics[name].type != "simple":
                continue
            for dialect in ("duckdb", "postgres", "bigquery", "snowflake"):
                request = QueryRequest(metric=name, date_range=Q3, time_grain="day")
                query = compile_query(manifest, validate(manifest, request), dialect=dialect)
                tree = parse_one(query.sql, dialect=dialect)
                assert tree.key == "select", f"{name}/{dialect} compiled to {tree.key}"
                assert ";" not in query.sql


def test_the_result_columns_are_declared(manifest):
    query = compiled(manifest, dimensions=["channel"], time_grain="month")
    assert query.columns == ["period", "channel", "revenue"]


def test_the_row_limit_is_injected_by_the_compiler(manifest):
    assert flat(compiled(manifest, row_limit=25).sql).endswith("LIMIT 25")
