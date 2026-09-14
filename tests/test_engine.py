"""Execution: the last step before the warehouse, and the first that can hang, flood or leak."""

import dataclasses
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from storefront_data import SCHEMA, storefront_database

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
        missing — or counting null keys from what came back — would be a false statement."""
        result = answer(
            manifest, engine, dimensions=["customer__region"], time_grain="month", row_limit=2
        )
        assert len(result["rows"]) == 2
        assert result["row_limit_reached"] is True
        assert result["missing_periods"] is None
        assert result["null_key_rows"] is None

    def test_each_join_reports_its_null_key_rows_beside_the_rows(self, manifest, engine):
        result = answer(manifest, engine, dimensions=["customer__region"])
        assert result["columns"] == ["customer__region", "revenue"]
        assert result["rows"] == [
            {"customer__region": "AMER", "revenue": "80.00"},
            {"customer__region": "EMEA", "revenue": "150.00"},
            {"customer__region": None, "revenue": "30.00"},  # NULL sorts last
        ]
        assert result["null_key_rows"] == {"customer": 1}

    def test_a_metric_with_no_joins_has_no_null_keys(self, manifest, engine):
        assert answer(manifest, engine)["null_key_rows"] == {}

    @pytest.mark.parametrize(
        ("metric", "date_range"),
        [
            ("average_order_value", Q3),
            ("trailing_12m_revenue", {"start_date": "2026-09-01", "end_date": "2026-09-30"}),
        ],
    )
    def test_shapes_that_do_not_count_null_keys_say_so(self, manifest, engine, metric, date_range):
        """`null`, not `{}`: these shapes do not count null keys yet, and silence is not zero."""
        result = answer(
            manifest,
            engine,
            metric=metric,
            date_range=date_range,
            dimensions=["customer__region"],
            time_grain="month",
        )
        assert result["rows"]
        assert result["null_key_rows"] is None


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
