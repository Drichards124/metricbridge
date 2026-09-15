"""Execution: the last step before the warehouse, and the first that can hang, flood or leak."""

import dataclasses
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from storefront_data import CUSTOMERS, ORDER_LINES, SCHEMA, storefront_database

from metricbridge.compiler import compile_query
from metricbridge.contract import MAX_ROW_LIMIT, QueryRequest, RefusalError, validate
from metricbridge.contract.context import GRAIN_ORDER
from metricbridge.engine import DuckDBEngine, execute
from metricbridge.manifest import load_manifest

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"
Q3 = {"start_date": "2026-07-01", "end_date": "2026-09-30"}
# A trillion-pair join with an impossible predicate: still running after 14 s when measured. A mere
# large `range` count finishes in 0.6 s, which would race the deadline rather than exceed it.
SLOW = "SELECT COUNT(*) FROM range(1000000) a, range(1000000) b WHERE a.range + b.range < 0"


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(STOREFRONT)


@pytest.fixture(scope="module")
def engine():
    return DuckDBEngine(storefront_database())


def answer(manifest, engine, **overrides):
    request = QueryRequest(**{"metric": "revenue", "date_range": Q3, **overrides})
    resolved = validate(manifest, request)
    return execute(manifest, resolved, compile_query(manifest, resolved, engine.dialect), engine)


def seeded(directory: Path, *statements: str, order_lines=ORDER_LINES) -> DuckDBEngine:
    """The storefront schema with these order lines, the usual customers, and any extra rows."""
    path = directory / "scratch.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(SCHEMA)
        if order_lines:  # DuckDB refuses executemany with no parameter sets
            connection.executemany(
                "INSERT INTO storefront.fct_order_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                order_lines,
            )
        connection.executemany(
            "INSERT INTO storefront.dim_customers VALUES (?, ?, ?, ?)", CUSTOMERS
        )
        for statement in statements:
            connection.execute(statement)
    return DuckDBEngine(path)


def code_of(error: RefusalError) -> str:
    (refusal,) = error.refusals
    return refusal.code


class Spy:
    dialect = "duckdb"

    def __init__(self):
        self.calls = []

    def execute(self, sql, parameters):
        self.calls.append((sql, parameters))
        return [("0.00",)]


class TestTheGuardrailRunsFirst:
    def test_an_unsafe_statement_never_reaches_the_engine(self, manifest):
        resolved = validate(manifest, QueryRequest(metric="revenue", date_range=Q3))
        query = compile_query(manifest, resolved)
        unlimited = dataclasses.replace(query, sql=query.sql.replace(" LIMIT 100", ""))
        spy = Spy()
        with pytest.raises(RefusalError) as refused:
            execute(manifest, resolved, unlimited, spy)
        assert code_of(refused.value) == "guardrail_violation"
        assert spy.calls == []

    def test_a_safe_statement_is_sent_exactly_as_compiled(self, manifest):
        resolved = validate(manifest, QueryRequest(metric="revenue", date_range=Q3))
        query = compile_query(manifest, resolved)
        spy = Spy()
        execute(manifest, resolved, query, spy)
        assert spy.calls == [(query.sql, query.parameters)]


class TestAnswers:
    def test_rows_are_named_by_the_compiler_and_decimals_stay_exact(self, manifest, engine):
        """A float turns £100.50 + £49.50 into something that is not quite £150."""
        result = answer(manifest, engine, time_grain="month")
        assert result["columns"] == ["period", "revenue"]
        assert result["rows"] == [
            {"period": "2026-07-01", "revenue": "230.00"},
            {"period": "2026-09-01", "revenue": "30.00"},  # the last day of the range counts
        ]

    def test_a_period_the_data_never_produced_is_reported(self, manifest, engine):
        """August had no orders. Absent from the rows, it would read as not asked for."""
        result = answer(manifest, engine, time_grain="month")
        assert result["missing_periods"] == ["2026-08-01"]
        assert result["row_limit_reached"] is False

    def test_a_trailing_total_reports_the_anchor_it_dropped(self, manifest, engine):
        """Anchor dropping (docs/failure-modes.md): October has no rows, so its trailing total is
        absent rather than 900. The gap is now said out loud."""
        result = answer(
            manifest,
            engine,
            metric="trailing_12m_revenue",
            date_range={"start_date": "2026-09-01", "end_date": "2026-10-31"},
            time_grain="month",
        )
        assert result["rows"] == [{"period": "2026-09-01", "trailing_12m_revenue": 900}]
        assert result["missing_periods"] == ["2026-10-01"]

    def test_an_answer_without_a_grain_has_no_periods_to_miss(self, manifest, engine):
        assert answer(manifest, engine)["missing_periods"] == []

    def test_nothing_is_claimed_about_rows_the_limit_cut_off(self, manifest, engine):
        """Three groups, a limit of two: September's row exists but was not returned, so calling it
        missing would be a false statement. The unreconciled totals were counted before the limit,
        so they still describe the whole answer — September's empty-key line included."""
        result = answer(
            manifest, engine, dimensions=["customer__region"], time_grain="month", row_limit=2
        )
        assert len(result["rows"]) == 2
        assert result["row_limit_reached"] is True
        assert result["missing_periods"] is None
        assert result["unreconciled"] == {
            "customer": {"rows": 1, "empty_key_rows": 1, "value": "30.00"}
        }

    def test_each_join_reports_its_unreconciled_rows_beside_the_rows(self, manifest, engine):
        result = answer(manifest, engine, dimensions=["customer__region"])
        assert result["columns"] == ["customer__region", "revenue"]
        assert result["rows"] == [
            {"customer__region": "AMER", "revenue": "80.00"},
            {"customer__region": "EMEA", "revenue": "150.00"},
            {"customer__region": None, "revenue": "30.00"},  # NULL sorts last
        ]
        assert result["unreconciled"] == {
            "customer": {"rows": 1, "empty_key_rows": 1, "value": "30.00"}
        }
        assert "null_key_rows" not in result

    def test_rows_no_joined_row_matches_are_totalled_per_entity(self, manifest, tmp_path):
        """An empty key and a key naming a customer who does not exist both land in the blank
        region. Both are unreconciled; the empty one is also counted apart, because it is a
        different fix."""
        orphan = (5, 4, 99, 1, "2026-09-15", "2026-09-15", "web", "20.00")
        engine = seeded(tmp_path, order_lines=[*ORDER_LINES, orphan])
        result = answer(manifest, engine, dimensions=["customer__region"])
        assert result["rows"][-1] == {"customer__region": None, "revenue": "50.00"}
        assert result["unreconciled"] == {
            "customer": {"rows": 2, "empty_key_rows": 1, "value": "50.00"}
        }

    def test_the_row_limit_does_not_change_the_totals(self, manifest, tmp_path):
        """The totals cover the whole scan, so a truncated answer still carries them exactly."""
        orphan = (5, 4, 99, 1, "2026-07-15", "2026-07-15", "web", "20.00")
        engine = seeded(tmp_path, order_lines=[*ORDER_LINES, orphan])
        cut = {"dimensions": ["customer__region"], "time_grain": "month"}
        whole = answer(manifest, engine, **cut)
        truncated = answer(manifest, engine, **cut, row_limit=1)
        assert truncated["row_limit_reached"] is True
        assert truncated["unreconciled"] == whole["unreconciled"]
        assert whole["unreconciled"] == {
            "customer": {"rows": 2, "empty_key_rows": 1, "value": "50.00"}
        }

    def test_a_distinct_count_is_not_summed_across_groups(self, manifest, tmp_path):
        """Order 3 has an empty-key line in September and an orphan line in July. Adding up the
        monthly groups counts it twice; it is one unreconciled order."""
        orphan = (5, 3, 99, 1, "2026-07-20", "2026-07-20", "web", "20.00")
        engine = seeded(tmp_path, order_lines=[*ORDER_LINES, orphan])
        result = answer(
            manifest,
            engine,
            metric="order_count",
            dimensions=["customer__region"],
            time_grain="month",
        )
        assert result["unreconciled"] == {"customer": {"rows": 2, "empty_key_rows": 1, "value": 1}}

    def test_an_average_is_taken_over_the_unmatched_rows_not_averaged_across_groups(
        self, manifest, tmp_path
    ):
        """£30 in September (empty key) and £20 and £10 in July (unknown customers) average to
        £20. Averaging the monthly averages would say £22.50."""
        orphans = [
            (5, 4, 99, 1, "2026-07-15", "2026-07-15", "web", "20.00"),
            (6, 5, 98, 1, "2026-07-16", "2026-07-16", "web", "10.00"),
        ]
        engine = seeded(tmp_path, order_lines=[*ORDER_LINES, *orphans])
        result = answer(
            manifest,
            engine,
            metric="average_line_value",
            dimensions=["customer__region"],
            time_grain="month",
        )
        assert result["unreconciled"] == {
            "customer": {"rows": 3, "empty_key_rows": 1, "value": 20.0}
        }

    def test_a_trailing_total_accounts_for_its_whole_lookback(self, manifest, tmp_path):
        """September's trailing twelve months are built from October 2025 onward, so an unknown
        customer's £50 in March shapes the answer and is counted, and the window says so."""
        engine = seeded(
            tmp_path,
            "INSERT INTO billing.fct_subscription_revenue_daily VALUES "
            + ", ".join(f"('2026-{month:02d}-01', 100, 1, 1)" for month in range(1, 10)),
            "INSERT INTO billing.fct_subscription_revenue_daily VALUES "
            "('2026-03-01', 50, 2, 99), ('2026-09-01', 25, 3, NULL)",
        )
        result = answer(
            manifest,
            engine,
            metric="trailing_12m_revenue",
            date_range={"start_date": "2026-09-01", "end_date": "2026-09-30"},
            dimensions=["customer__region"],
            time_grain="month",
        )
        assert result["rows"] == [
            {"period": "2026-09-01", "customer__region": "EMEA", "trailing_12m_revenue": 900},
            {"period": "2026-09-01", "customer__region": None, "trailing_12m_revenue": 75},
        ]
        assert result["unreconciled"] == {"customer": {"rows": 2, "empty_key_rows": 1, "value": 75}}
        assert result["unreconciled_window"] == {
            "start_date": "2025-10-01",
            "end_date": "2026-09-30",
        }

    def test_each_half_of_a_ratio_accounts_for_the_rows_it_read(self, manifest, tmp_path):
        """Web revenue over all orders: the numerator reads web lines only, so the empty-key line
        (no channel) is not in it, while the denominator counts every order. One `rows` for the
        ratio would be true of one half and false of the other."""
        orphan = (5, 4, 99, 1, "2026-09-15", "2026-09-15", "web", "20.00")
        engine = seeded(tmp_path, order_lines=[*ORDER_LINES, orphan])
        result = answer(
            manifest, engine, metric="online_takings_ratio", dimensions=["customer__region"]
        )
        assert result["unreconciled"] == {
            "customer": {
                "numerator": {"rows": 1, "empty_key_rows": 0, "value": "20.00"},
                "denominator": {"rows": 2, "empty_key_rows": 1, "value": 2},
            }
        }

    def test_a_snapshot_counts_only_the_rows_it_chose(self, manifest, tmp_path):
        """Month-end stock reads the last snapshot per product. Product 99 does not exist and one
        row has no product; the 100 units product 99 held on 10 July are not month-end stock."""
        engine = seeded(
            tmp_path,
            "INSERT INTO storefront.dim_products VALUES (1, 'toys', 'EU')",
            "INSERT INTO warehouse.fct_inventory_daily VALUES "
            "(1, '2026-07-01', 'north', 5), (1, '2026-07-31', 'north', 7), "
            "(99, '2026-07-10', 'north', 100), (99, '2026-07-31', 'north', 3), "
            "(NULL, '2026-07-31', 'north', 2)",
        )
        result = answer(
            manifest,
            engine,
            metric="inventory_on_hand",
            date_range={"start_date": "2026-07-01", "end_date": "2026-07-31"},
            dimensions=["product__category"],
            time_grain="month",
        )
        assert result["rows"] == [
            {"period": "2026-07-01", "product__category": "toys", "inventory_on_hand": 7},
            {"period": "2026-07-01", "product__category": None, "inventory_on_hand": 5},
        ]
        assert result["unreconciled"] == {"product": {"rows": 2, "empty_key_rows": 1, "value": 5}}

    def test_a_metric_with_no_joins_has_nothing_to_reconcile(self, manifest, engine):
        """`{}`: nothing was joined, so no row can have failed to match."""
        result = answer(manifest, engine)
        assert result["unreconciled"] == {}
        assert result["unreconciled_window"] == {
            "start_date": "2026-07-01",
            "end_date": "2026-09-30",
        }

    def test_an_answer_with_no_rows_claims_nothing(self, manifest, engine):
        """`null`, not zeros: with no row to carry the totals, nothing was counted. January has no
        orders in the seed."""
        result = answer(
            manifest,
            engine,
            date_range={"start_date": "2026-01-01", "end_date": "2026-01-31"},
            dimensions=["customer__region"],
        )
        assert result["rows"] == []
        assert result["unreconciled"] is None
        assert result["unreconciled_window"] is None


class TestTheConnection:
    def test_a_slow_statement_is_stopped_at_the_deadline(self):
        engine = DuckDBEngine(storefront_database(), statement_timeout=0.2)
        started = time.monotonic()
        with pytest.raises(RefusalError) as refused:
            engine.execute(SLOW, {})
        assert code_of(refused.value) == "statement_timeout"
        assert time.monotonic() - started < 5

    def test_a_timeout_stops_only_its_own_statement(self):
        """Requests share the connection. Interrupting the connection rather than the statement's
        own cursor would kill whatever else happened to be running."""
        engine = DuckDBEngine(storefront_database(), statement_timeout=0.3)
        outcomes = []

        def slow():
            try:
                engine.execute(SLOW, {})
            except RefusalError as error:
                outcomes.append(code_of(error))

        thread = threading.Thread(target=slow)
        thread.start()
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            assert engine.execute("SELECT 1", {}) == [(1,)]
        thread.join()
        assert outcomes == ["statement_timeout"]

    def test_no_timer_outlives_its_statement(self):
        engine = DuckDBEngine(storefront_database(), statement_timeout=30)
        engine.execute("SELECT 1", {})
        timers = [t for t in threading.enumerate() if isinstance(t, threading.Timer)]
        for timer in timers:
            timer.join(1)
        assert not any(timer.is_alive() for timer in timers)

    @settings(max_examples=40, deadline=None)
    @given(st.integers(min_value=0, max_value=2 * MAX_ROW_LIMIT))
    def test_no_answer_is_ever_larger_than_the_row_cap(self, engine, rows):
        """A backstop behind the compiler's limit and the guardrail: it fails closed, never
        truncates, because a partial answer reads as a whole one."""
        statement = f"SELECT range FROM range({rows})"
        if rows <= MAX_ROW_LIMIT:
            assert len(engine.execute(statement, {})) == rows
        else:
            with pytest.raises(RefusalError) as refused:
                engine.execute(statement, {})
            assert code_of(refused.value) == "row_cap_exceeded"

    def test_an_open_database_without_the_lockdown_is_never_reused(self, tmp_path):
        """DuckDB shares one instance per file within a process. An engine that joined an
        instance someone opened unlocked would inherit file access nobody checked."""
        path = tmp_path / "shared.duckdb"
        duckdb.connect(str(path)).close()
        unlocked = duckdb.connect(str(path), read_only=True)
        with pytest.raises(duckdb.ConnectionException):
            DuckDBEngine(path)
        unlocked.close()

    def test_engines_on_the_same_database_coexist(self):
        first = DuckDBEngine(storefront_database())
        second = DuckDBEngine(storefront_database())
        assert first.execute("SELECT 1", {}) == second.execute("SELECT 1", {})

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT * FROM read_csv('/etc/hosts')",
            "SET enable_external_access = true",
            "INSERT INTO storefront.dim_customers VALUES (3, 'APAC', 'smb', '2026-01-01')",
        ],
    )
    def test_the_connection_cannot_read_files_write_or_unlock(self, engine, statement):
        """Defence in depth: even a statement that got past the guardrail finds nothing to do."""
        with pytest.raises(RefusalError) as refused:
            engine.execute(statement, {})
        assert code_of(refused.value) == "execution_failed"

    def test_a_driver_error_carries_no_values_to_the_agent_or_the_log(self, engine, caplog):
        """DuckDB quotes the offending value in its message. The class and the statement are
        enough to debug from; the statement holds only placeholders."""
        with pytest.raises(RefusalError) as refused:
            engine.execute("SELECT CAST($v AS INTEGER)", {"v": "O'Brien secret"})
        assert code_of(refused.value) == "execution_failed"
        assert "secret" not in str(refused.value.payload())
        assert "ConversionException" in caplog.text
        assert "CAST($v AS INTEGER)" in caplog.text
        assert "secret" not in caplog.text


class Scratch:
    """A writable in-memory DuckDB behind the engine interface, seeded per generated example."""

    dialect = "duckdb"

    def __init__(self, days: set[int]):
        self.connection = duckdb.connect()
        self.connection.execute(SCHEMA)
        rows = [
            (n, n, 1, 1, date(2026, 7, 1) + timedelta(days=n), None, "web", "1.00") for n in days
        ]
        if rows:
            self.connection.executemany(
                "INSERT INTO storefront.fct_order_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
            )

    def execute(self, sql, parameters):
        return self.connection.execute(sql, parameters).fetchall()


@settings(max_examples=60, deadline=None)
@given(
    days=st.sets(st.integers(min_value=0, max_value=91)),
    grain=st.sampled_from(GRAIN_ORDER),
    row_limit=st.integers(min_value=1, max_value=100),
)
def test_every_expected_period_is_returned_or_reported_missing(manifest, days, grain, row_limit):
    """Over any pattern of active days in Q3: returned and missing periods partition the expected
    ones exactly — unless the limit cut the answer, in which case nothing is claimed."""
    resolved = validate(
        manifest,
        QueryRequest(metric="revenue", date_range=Q3, time_grain=grain, row_limit=row_limit),
    )
    query = compile_query(manifest, resolved)
    result = execute(manifest, resolved, query, Scratch(days))
    returned = {row["period"] for row in result["rows"]}
    expected = {period.isoformat() for period in query.expected_periods}
    assert returned <= expected
    if result["row_limit_reached"]:
        assert result["missing_periods"] is None
    else:
        missing = set(result["missing_periods"])
        assert returned | missing == expected
        assert not returned & missing


# Customers 1 and 2 exist; 98 and 99 do not; None is an empty key.
KEYS = st.sampled_from([1, 2, 98, 99, None])


@settings(max_examples=40, deadline=None)
@given(
    lines=st.lists(
        st.tuples(KEYS, st.integers(min_value=0, max_value=91), st.integers(0, 50_000)),
        max_size=12,
    ),
    row_limit=st.integers(min_value=1, max_value=5),
)
def test_unreconciled_totals_match_a_recount_whatever_the_limit(manifest, lines, row_limit):
    """Over any mix of matched, empty and unknown keys, the totals equal a plain recount of the
    lines — and a limit that cuts the answer changes nothing about them."""
    order_lines = [
        (n, n, key, 1, date(2026, 7, 1) + timedelta(days=day), None, "web", f"{pence / 100:.2f}")
        for n, (key, day, pence) in enumerate(lines)
    ]
    unmatched = [(key, pence) for key, _, pence in lines if key not in (1, 2)]
    expected = {
        "customer": {
            "rows": len(unmatched),
            "empty_key_rows": sum(1 for key, _ in unmatched if key is None),
            "value": f"{sum(p for _, p in unmatched) / 100:.2f}" if unmatched else None,
        }
    }
    with tempfile.TemporaryDirectory() as directory:
        engine = seeded(Path(directory), order_lines=order_lines)
        cut = {"dimensions": ["customer__region"], "time_grain": "month"}
        whole = answer(manifest, engine, **cut)
        limited = answer(manifest, engine, **cut, row_limit=row_limit)
    if not lines:
        assert whole["unreconciled"] is None
        return
    assert whole["unreconciled"] == expected
    assert limited["unreconciled"] == expected
