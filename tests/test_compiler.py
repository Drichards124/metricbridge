"""The compiler turns a resolved request into one parameterised statement.

The agent never writes SQL and never sees a database. Golden statements are checked in, so a change
that alters generated SQL shows up as a diff in review — which is how you find out you changed a
number before shipping it.
"""

import re
from datetime import date
from pathlib import Path

import duckdb
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
            "WITH answer AS ("
            "SELECT DATE_TRUNC('MONTH', orders.order_date) AS period, "
            "customer.region AS customer__region, "
            "SUM(orders.amount) AS revenue "
            "FROM storefront.fct_order_lines AS orders "
            "LEFT JOIN storefront.dim_customers AS customer "
            "ON orders.customer_id = customer.customer_id "
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date "
            "GROUP BY DATE_TRUNC('MONTH', orders.order_date), customer.region"
            "), totals AS ("
            "SELECT SUM(CASE WHEN customer.customer_id IS NULL THEN 1 ELSE 0 END) "
            "AS customer__unreconciled_rows, "
            "SUM(CASE WHEN orders.customer_id IS NULL THEN 1 ELSE 0 END) "
            "AS customer__empty_key_rows, "
            "SUM(CASE WHEN customer.customer_id IS NULL THEN orders.amount END) "
            "AS customer__unreconciled_value "
            "FROM storefront.fct_order_lines AS orders "
            "LEFT JOIN storefront.dim_customers AS customer "
            "ON orders.customer_id = customer.customer_id "
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date"
            ") "
            "SELECT answer.period, answer.customer__region, answer.revenue, "
            "totals.customer__unreconciled_rows, "
            "totals.customer__empty_key_rows, totals.customer__unreconciled_value "
            "FROM answer CROSS JOIN totals "
            "ORDER BY revenue DESC, period ASC, customer__region ASC "
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

    @pytest.mark.parametrize(
        "dialect", ["duckdb", "postgres", "bigquery", "snowflake", "clickhouse"]
    )
    def test_every_dialect_carries_the_unreconciled_totals(self, manifest, dialect):
        """Plain CASE and SUM inside a CTE: one spelling, valid on every engine. ClickHouse also
        needs `join_use_nulls = 1` for an unmatched key to read as NULL; its 1.7 adapter sets it."""
        sql = flat(compiled(manifest, dialect=dialect, dimensions=["customer__region"]).sql)
        assert (
            "totals AS (SELECT SUM(CASE WHEN customer.customer_id IS NULL THEN 1 ELSE 0 END) "
            "AS customer__unreconciled_rows, "
            "SUM(CASE WHEN orders.customer_id IS NULL THEN 1 ELSE 0 END) "
            "AS customer__empty_key_rows, "
            "SUM(CASE WHEN customer.customer_id IS NULL THEN orders.amount END) "
            "AS customer__unreconciled_value"
        ) in sql
        assert "FROM answer CROSS JOIN totals" in sql


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


class TestRowOrder:
    """The limit keeps whichever rows sort first, so an unstated order is an unstated answer — and
    1.7 compares engines row by row."""

    def test_rows_come_back_in_key_order_when_no_order_is_asked(self, manifest):
        sql = flat(compiled(manifest, dimensions=["channel"], time_grain="month").sql)
        assert sql.endswith("ORDER BY period ASC, channel ASC LIMIT 100")

    def test_a_requested_order_keeps_the_keys_as_tie_breakers(self, manifest):
        sql = flat(
            compiled(
                manifest,
                dimensions=["channel"],
                time_grain="month",
                order_by=[{"field": "channel", "direction": "asc"}],
            ).sql
        )
        assert sql.endswith("ORDER BY channel ASC, period ASC LIMIT 100")

    def test_every_shape_is_ordered_by_its_keys(self, manifest):
        for metric in ("average_order_value", "inventory_on_hand", "trailing_12m_revenue"):
            sql = flat(compiled(manifest, metric=metric, time_grain="month").sql)
            assert sql.endswith("ORDER BY period ASC LIMIT 100"), metric

    @pytest.mark.parametrize(
        ("dialect", "direction", "expected"),
        [
            ("postgres", "desc", "ORDER BY channel DESC NULLS LAST"),
            ("snowflake", "desc", "ORDER BY channel DESC NULLS LAST"),
            ("bigquery", "asc", "ORDER BY channel ASC NULLS LAST"),
        ],
    )
    def test_nulls_sort_last_on_every_dialect(self, manifest, dialect, direction, expected):
        """Engines disagree on where NULL sorts, so which rows survive the limit would differ. The
        clause is omitted only where NULLS LAST is already that engine's default."""
        query = compiled(
            manifest,
            dialect=dialect,
            dimensions=["channel"],
            order_by=[{"field": "channel", "direction": direction}],
        )
        assert expected in flat(query.sql)


class TestRatioMetrics:
    def test_a_ratio_divides_two_aggregates_after_grouping(self, manifest):
        """Dividing sums, never summing ratios: the average of averages is a different number."""
        query = compiled(manifest, metric="average_order_value")
        assert flat(query.sql) == (
            "WITH numerator AS ("
            "SELECT SUM(orders.amount) AS value FROM storefront.fct_order_lines AS orders "
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date"
            "), denominator AS ("
            "SELECT COUNT(DISTINCT orders.order_id) AS value "
            "FROM storefront.fct_order_lines AS orders "
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date"
            ") "
            "SELECT COALESCE(numerator.value, 0) / NULLIF(denominator.value, 0) "
            "AS average_order_value "
            "FROM denominator CROSS JOIN numerator "
            "LIMIT 100"
        )

    def test_a_ratio_joins_its_legs_on_every_group_key(self, manifest):
        sql = flat(
            compiled(
                manifest, metric="average_order_value", dimensions=["channel"], time_grain="month"
            ).sql
        )
        assert "GROUP BY DATE_TRUNC('MONTH', orders.order_date), orders.channel" in sql
        assert (
            "FROM denominator LEFT JOIN numerator "
            "ON denominator.period = numerator.period AND denominator.channel = numerator.channel"
        ) in sql
        assert "SELECT denominator.period AS period, denominator.channel AS channel" in sql

    def test_a_zero_denominator_yields_null_not_zero(self, manifest):
        """No orders is not an average order value of nothing."""
        assert (
            "NULLIF(denominator.value, 0)" in compiled(manifest, metric="average_order_value").sql
        )

    def test_the_result_columns_cover_both_legs(self, manifest):
        query = compiled(manifest, metric="average_order_value", time_grain="month")
        assert query.columns == ["period", "average_order_value"]


class TestSnapshotMetrics:
    def test_a_declared_rollup_takes_the_periods_last_snapshot(self, manifest):
        """Summing daily stock counts the same pallet once per day; month-end is one row per
        product, chosen by the declared window."""
        query = compiled(manifest, metric="inventory_on_hand", time_grain="month")
        assert flat(query.sql) == (
            "WITH ranked AS ("
            "SELECT DATE_TRUNC('MONTH', inventory_snapshots.snapshot_date) AS period, "
            "inventory_snapshots.units AS value, "
            "ROW_NUMBER() OVER ("
            "PARTITION BY DATE_TRUNC('MONTH', inventory_snapshots.snapshot_date), "
            "inventory_snapshots.product_id "
            "ORDER BY inventory_snapshots.snapshot_date DESC) AS position "
            "FROM warehouse.fct_inventory_daily AS inventory_snapshots "
            "WHERE inventory_snapshots.snapshot_date >= $start_date "
            "AND inventory_snapshots.snapshot_date < $end_date"
            ") "
            "SELECT period, SUM(value) AS inventory_on_hand "
            "FROM ranked WHERE position = 1 GROUP BY period "
            "ORDER BY period ASC "
            "LIMIT 100"
        )

    def test_the_window_choice_decides_which_snapshot_wins(self, manifest):
        opening = flat(compiled(manifest, metric="opening_stock", time_grain="month").sql)
        assert "ORDER BY inventory_snapshots.snapshot_date ASC) AS position" in opening
        closing = flat(compiled(manifest, metric="inventory_on_hand", time_grain="month").sql)
        assert "ORDER BY inventory_snapshots.snapshot_date DESC) AS position" in closing

    def test_without_a_grain_the_latest_snapshot_in_the_range_is_taken(self, manifest):
        sql = flat(compiled(manifest, metric="inventory_on_hand").sql)
        assert "PARTITION BY inventory_snapshots.product_id" in sql
        assert "DATE_TRUNC" not in sql

    def test_a_requested_cut_is_carried_through_the_ranking(self, manifest):
        sql = flat(
            compiled(
                manifest, metric="inventory_on_hand", dimensions=["warehouse"], time_grain="month"
            ).sql
        )
        assert "inventory_snapshots.warehouse AS warehouse" in sql
        assert "GROUP BY period, warehouse" in sql


def test_every_metric_shape_compiles_to_one_parseable_statement(manifest):
    for name, metric in manifest.metrics.items():
        if metric.type == "cumulative":
            continue  # 1.4c
        for dialect in ("duckdb", "postgres", "bigquery", "snowflake"):
            request = QueryRequest(metric=name, date_range=Q3, time_grain="month")
            if name in ("stock_level",):
                request = QueryRequest(metric=name, date_range=Q3, time_grain="day")
            query = compile_query(manifest, validate(manifest, request), dialect=dialect)
            assert parse_one(query.sql, dialect=dialect).key == "select", f"{name}/{dialect}"
            assert ";" not in query.sql


class TestCumulativeMetrics:
    def test_a_trailing_window_joins_rows_to_every_anchor_period(self, manifest):
        query = compiled(manifest, metric="trailing_12m_revenue", time_grain="month")
        assert flat(query.sql) == (
            "WITH periods AS ("
            "SELECT DISTINCT DATE_TRUNC('MONTH', subscription_revenue.revenue_date) AS period "
            "FROM billing.fct_subscription_revenue_daily AS subscription_revenue "
            "WHERE subscription_revenue.revenue_date >= $start_date "
            "AND subscription_revenue.revenue_date < $end_date"
            "), measured AS ("
            "SELECT subscription_revenue.revenue_date AS occurred_at, "
            "subscription_revenue.amount AS value "
            "FROM billing.fct_subscription_revenue_daily AS subscription_revenue "
            "WHERE subscription_revenue.revenue_date >= $scan_start "
            "AND subscription_revenue.revenue_date < $end_date"
            ") "
            "SELECT periods.period AS period, SUM(measured.value) AS trailing_12m_revenue "
            "FROM periods JOIN measured "
            "ON measured.occurred_at >= periods.period - INTERVAL 11 MONTH "
            "AND measured.occurred_at < periods.period + INTERVAL 1 MONTH "
            "GROUP BY periods.period "
            "ORDER BY period ASC "
            "LIMIT 100"
        )

    def test_the_scan_reads_back_further_than_the_answer(self, manifest):
        """A trailing-twelve-month figure for Q3 must read a year: pruning to the output window
        returns a wrong number, which is worse than the wide scan the rule prevents."""
        query = compiled(manifest, metric="trailing_12m_revenue", time_grain="month")
        assert query.parameters["scan_start"] == date(2025, 8, 1)
        assert query.parameters["start_date"] == date(2026, 7, 1)
        assert query.output_window == (date(2026, 7, 1), date(2026, 10, 1))
        assert query.scan_window == (date(2025, 8, 1), date(2026, 10, 1))

    def test_grain_to_date_anchors_at_the_start_of_each_period(self, manifest):
        sql = flat(compiled(manifest, metric="revenue_month_to_date", time_grain="month").sql)
        assert "ON measured.occurred_at >= periods.period" in sql
        assert "measured.occurred_at < periods.period + INTERVAL 1 MONTH" in sql
        assert "INTERVAL 11" not in sql

    def test_without_a_grain_one_window_ending_at_the_range_end(self, manifest):
        query = compiled(manifest, metric="trailing_12m_revenue")
        sql = flat(query.sql)
        assert "periods" not in sql
        assert "SUM(subscription_revenue.amount) AS trailing_12m_revenue" in sql
        # The window ends where the request ends, so twelve months back is October, not August:
        # anchoring it to the request start would answer a different question.
        assert query.parameters["start_date"] == date(2025, 10, 1)
        assert query.scan_window == (date(2025, 10, 1), date(2026, 10, 1))

    def test_anchors_are_never_grouped_by_a_dimension(self, manifest):
        """`periods` must be the bucket alone: grouping it by a cut means a country with no rows
        that month stops anchoring, and its trailing total disappears rather than rolling over."""
        sql = flat(
            compiled(
                manifest,
                metric="trailing_12m_revenue",
                dimensions=["customer__segment"],
                time_grain="month",
            ).sql
        )
        periods_cte = sql.split("), measured AS (")[0]
        assert "segment" not in periods_cte
        assert "customer.segment AS customer__segment" in sql
        assert "GROUP BY periods.period, measured.customer__segment" in sql


class TestCumulativeAgainstDuckDB:
    """The generated SQL is executed, because a golden statement only proves what we wrote."""

    def _seeded(self):
        connection = duckdb.connect()
        connection.execute(
            "CREATE TABLE billing.fct_subscription_revenue_daily"
            "(revenue_date DATE, amount INTEGER, subscription_id INTEGER, customer_id INTEGER)"
            if False
            else "CREATE SCHEMA billing; "
            "CREATE TABLE billing.fct_subscription_revenue_daily "
            "(revenue_date DATE, amount INTEGER, subscription_id INTEGER, customer_id INTEGER)"
        )
        rows = []
        for month in range(1, 10):  # Jan–Sep 2026, £100 on the first of each month
            rows.append((f"2026-{month:02d}-01", 100, 1, 1))
        connection.executemany(
            "INSERT INTO billing.fct_subscription_revenue_daily VALUES (?, ?, ?, ?)", rows
        )
        return connection

    def test_a_trailing_total_accumulates_across_periods(self, manifest):
        query = compiled(manifest, metric="trailing_12m_revenue", time_grain="month")
        result = self._seeded().execute(query.sql, query.parameters).fetchall()
        by_period = {row[0].strftime("%Y-%m"): row[1] for row in result}
        # Q3 anchors see every month since January, so the total climbs by 100 each month.
        assert by_period == {"2026-07": 700, "2026-08": 800, "2026-09": 900}

    def test_an_inactive_period_drops_its_anchor(self, manifest):
        """The known gap, executed: October has no rows, so its trailing total is absent rather
        than rolling forward. Recorded in docs/failure-modes.md as anchor dropping."""
        request = QueryRequest(
            metric="trailing_12m_revenue",
            date_range={"start_date": "2026-09-01", "end_date": "2026-10-31"},
            time_grain="month",
        )
        query = compile_query(manifest, validate(manifest, request))
        periods = [
            row[0].strftime("%Y-%m")
            for row in self._seeded().execute(query.sql, query.parameters).fetchall()
        ]
        assert periods == ["2026-09"]  # October is missing, not 900
        assert query.expected_periods == [date(2026, 9, 1), date(2026, 10, 1)]

    def test_row_multiplication_at_daily_grain_is_bounded_by_the_window(self, manifest):
        """A range join replicates each row once per anchor it falls into. At daily grain with a
        long window that is the cost of correctness, and it should be measured, not assumed."""
        query = compiled(
            manifest,
            metric="trailing_12m_revenue",
            time_grain="day",
            date_range={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        )
        connection = self._seeded()
        joined = connection.execute(
            "SELECT COUNT(*) FROM ("
            + query.sql.replace("SUM(measured.value)", "measured.value").replace(
                "GROUP BY periods.period", ""
            )
            + ")",
            query.parameters,
        ).fetchone()[0]
        anchors = 31  # one per day in July
        assert joined <= anchors * 7  # seven rows in the scan window, each seen once per anchor
