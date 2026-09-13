"""The last check before the warehouse, run on the tree rather than the text.

We check SQL we generated ourselves, on purpose. That looks redundant until you notice the compiler
is the component most likely to contain the bug: a mishandled filter, a dropped predicate on an edge
case. Defence in depth means the last thing before execution validates the artifact, not the process
that produced it.

String matching would fail on the first comment, quoted literal or subquery — and it fails *open*,
which is the wrong direction.
"""

from pathlib import Path

import pytest

from metricbridge.compiler import compile_query
from metricbridge.contract import QueryRequest, RefusalError, validate
from metricbridge.guardrail import assert_safe
from metricbridge.manifest import load_manifest

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"
Q3 = {"start_date": "2026-07-01", "end_date": "2026-09-30"}


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(STOREFRONT)


def compiled(manifest, **overrides):
    request = QueryRequest(**{"metric": "revenue", "date_range": Q3, **overrides})
    return compile_query(manifest, validate(manifest, request))


def refuse(manifest, sql, dialect="duckdb"):
    with pytest.raises(RefusalError) as refused:
        assert_safe(sql, manifest, dialect=dialect)
    (refusal,) = refused.value.refusals
    assert refusal.code == "guardrail_violation"
    assert refusal.remediation
    return refusal


class TestWhatTheCompilerProduces:
    @pytest.mark.parametrize(
        "metric",
        [
            "revenue",
            "order_count",
            "average_order_value",
            "inventory_on_hand",
            "trailing_12m_revenue",
        ],
    )
    def test_every_compiled_statement_passes(self, manifest, metric):
        """The guardrails must not refuse the compiler's own output, or nothing ships."""
        query = compiled(manifest, metric=metric, time_grain="month")
        assert_safe(query.sql, manifest, dialect=query.dialect)

    @pytest.mark.parametrize("dialect", ["duckdb", "postgres", "bigquery", "snowflake"])
    def test_it_holds_in_every_dialect(self, manifest, dialect):
        request = QueryRequest(metric="revenue", date_range=Q3, dimensions=["customer__region"])
        query = compile_query(manifest, validate(manifest, request), dialect=dialect)
        assert_safe(query.sql, manifest, dialect=dialect)


class TestMutants:
    """Take a statement the compiler produced, break it as a bug would, and check it is caught."""

    def test_a_dropped_date_bound_is_refused(self, manifest):
        sql = compiled(manifest).sql.replace(
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date",
            "WHERE 1 = 1",
        )
        assert "unbounded" in refuse(manifest, sql).message

    def test_a_bound_lost_inside_a_cte_leg_is_refused(self, manifest):
        """Checking only the outer query lets an unbounded CTE through while the statement looks
        compliant — the exact hole this walk exists to close."""
        query = compiled(manifest, metric="average_order_value")
        sql = query.sql.replace(
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date", "", 1
        )
        assert "unbounded" in refuse(manifest, sql).message

    def test_an_undeclared_table_is_refused(self, manifest):
        sql = compiled(manifest).sql.replace(
            "storefront.fct_order_lines", "storefront.fct_order_lines_shadow"
        )
        assert "not declared" in refuse(manifest, sql).message

    def test_a_stacked_statement_is_refused(self, manifest):
        sql = compiled(manifest).sql + "; DROP TABLE storefront.fct_order_lines"
        assert "one statement" in refuse(manifest, sql).message

    @pytest.mark.parametrize(
        "statement",
        [
            "DELETE FROM storefront.fct_order_lines",
            "UPDATE storefront.fct_order_lines SET amount = 0",
            "INSERT INTO storefront.fct_order_lines VALUES (1)",
            "DROP TABLE storefront.fct_order_lines",
            "CREATE TABLE x AS SELECT 1",
        ],
    )
    def test_anything_that_is_not_a_select_is_refused(self, manifest, statement):
        assert "SELECT" in refuse(manifest, statement).message

    def test_select_star_is_refused(self, manifest):
        sql = compiled(manifest).sql.replace("SUM(orders.amount) AS revenue", "*")
        assert "SELECT *" in refuse(manifest, sql).message

    def test_a_missing_limit_is_refused(self, manifest):
        sql = compiled(manifest).sql.replace(" LIMIT 100", "")
        assert "row limit" in refuse(manifest, sql).message

    def test_a_limit_above_the_ceiling_is_refused(self, manifest):
        sql = compiled(manifest).sql.replace("LIMIT 100", "LIMIT 100000")
        assert "ceiling" in refuse(manifest, sql).message

    def test_a_comment_cannot_smuggle_a_predicate_past_the_check(self, manifest):
        """String matching would see the bound in the comment and pass. The tree does not."""
        sql = compiled(manifest).sql.replace(
            "WHERE orders.order_date >= $start_date AND orders.order_date < $end_date",
            "WHERE 1 = 1 /* orders.order_date >= $start_date AND orders.order_date < $end_date */",
        )
        assert "unbounded" in refuse(manifest, sql).message


class TestAdversarialRequests:
    """The refusals an agent actually provokes, end to end from request to statement."""

    def test_a_request_with_no_date_range_never_reaches_sql(self, manifest):
        with pytest.raises(RefusalError) as refused:
            validate(manifest, QueryRequest(metric="revenue"))
        assert refused.value.refusals[0].code == "missing_partition_filter"

    def test_an_injection_in_a_filter_value_survives_the_guardrails_as_data(self, manifest):
        query = compiled(
            manifest,
            filters=[{"field": "channel", "operator": "=", "value": "web'; DROP TABLE orders; --"}],
        )
        assert_safe(query.sql, manifest, dialect=query.dialect)
        assert "DROP TABLE" not in query.sql
        assert query.parameters["filter_0"].endswith("--")

    def test_a_row_limit_above_the_ceiling_is_refused_before_compiling(self, manifest):
        with pytest.raises(RefusalError) as refused:
            validate(manifest, QueryRequest(metric="revenue", date_range=Q3, row_limit=100_000))
        assert refused.value.refusals[0].code == "row_limit_exceeded"

    def test_a_snapshot_summed_across_time_never_reaches_sql(self, manifest):
        with pytest.raises(RefusalError) as refused:
            validate(
                manifest, QueryRequest(metric="stock_level", date_range=Q3, time_grain="month")
            )
        assert refused.value.refusals[0].code == "non_additive_cut"
