"""Execution: the one place a compiled statement meets a warehouse.

Whether an answer can be trusted is decided before this — validation, compilation, the guardrail.
What is left is what a warehouse can still do wrong to an agent: run forever, return more than
anyone asked for, or say something in an error message that was never meant to leave it.

The answer is shaped so that an absence says so. A period the data never produced is listed, not
left as a gap; a row whose join key is empty is counted; and when the row limit cut the answer off,
nothing is claimed about the rows that did not come back.
"""

import logging
import threading
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import duckdb

from .compiler import CompiledQuery
from .contract import MAX_ROW_LIMIT, Refusal, RefusalError, Resolved
from .guardrail import assert_safe
from .manifest import SemanticManifest

EXECUTION_CODES = frozenset({"statement_timeout", "row_cap_exceeded", "execution_failed"})
NULL_KEY_SUFFIX = "__null_key_rows"

log = logging.getLogger(__name__)


class Engine(Protocol):
    dialect: str

    def execute(self, sql: str, parameters: dict[str, object]) -> list[tuple]: ...


def _refuse(code: str, message: str, remediation: str) -> RefusalError:
    return RefusalError([Refusal(code=code, message=message, remediation=remediation)])


class DuckDBEngine:
    """A DuckDB database opened read-only, with file access off and its configuration locked.

    DuckDB has no statement-timeout or row-cap setting, so both are enforced here — set by the
    operator when the server starts, never by the request.
    """

    dialect = "duckdb"

    def __init__(self, path: Path, *, statement_timeout: float = 30.0) -> None:
        self.statement_timeout = statement_timeout
        # At connect, not SET afterwards: DuckDB shares one instance per file within a process and
        # refuses a connection whose configuration differs, so an instance opened without the
        # lockdown is never silently reused.
        self._connection = duckdb.connect(
            str(path),
            read_only=True,
            config={"enable_external_access": False, "lock_configuration": True},
        )

    def execute(self, sql: str, parameters: dict[str, object]) -> list[tuple]:
        # A cursor of its own, so the deadline interrupts this statement and nothing else that
        # shares the connection.
        cursor = self._connection.cursor()
        timer = threading.Timer(self.statement_timeout, cursor.interrupt)
        timer.start()
        try:
            rows = cursor.execute(sql, parameters).fetchmany(MAX_ROW_LIMIT + 1)
        except duckdb.InterruptException:
            raise _refuse(
                "statement_timeout",
                f"the statement ran longer than {self.statement_timeout:g}s and was stopped.",
                "Narrow the date range or drop a cut, then ask again.",
            ) from None
        except duckdb.Error as error:
            # The driver's message can quote a bound value, so neither the agent nor the log sees
            # it. The statement is safe to log: its values are placeholders.
            log.error("execution_failed: %s while running: %s", type(error).__name__, sql)
            raise _refuse(
                "execution_failed",
                "the database could not run the statement.",
                "The request cannot fix this; report it to the operator of this gateway.",
            ) from None
        finally:
            timer.cancel()
            cursor.close()
        if len(rows) > MAX_ROW_LIMIT:
            raise _refuse(
                "row_cap_exceeded",
                f"the statement returned more than {MAX_ROW_LIMIT} rows.",
                "Nothing was returned: a partial answer reads as a whole one. Report it.",
            )
        return rows


def _as_date(value: object) -> object:
    return value.date() if isinstance(value, datetime) else value


def _normalise(column: str, value: object) -> object:
    """JSON-safe without losing anything: a float would turn £150.00 into something near it."""
    if column == "period" and value is not None:
        return _as_date(value).isoformat()  # a bucket is a day; engines disagree on its type
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


def execute(
    manifest: SemanticManifest, resolved: Resolved, query: CompiledQuery, engine: Engine
) -> dict[str, Any]:
    """Assert the statement, run it, and state every absence in the answer."""
    assert_safe(query.sql, manifest, dialect=engine.dialect)  # immediately before, never after
    raw = engine.execute(query.sql, query.parameters)
    rows = [dict(zip(query.columns, row, strict=True)) for row in raw]
    columns = [c for c in query.columns if not c.endswith(NULL_KEY_SUFFIX)]
    limited = len(rows) >= resolved.row_limit

    missing_periods = None
    null_key_rows = None
    if not limited:
        returned = {_as_date(row["period"]) for row in rows} if "period" in columns else set()
        missing_periods = [p.isoformat() for p in query.expected_periods if p not in returned]
        # Ratio and cumulative shapes do not count null keys yet: `null`, because `{}` reads as
        # "none found".
        if resolved.metric.type == "simple":
            null_key_rows = {
                c.removesuffix(NULL_KEY_SUFFIX): sum(row[c] or 0 for row in rows)
                for c in query.columns
                if c.endswith(NULL_KEY_SUFFIX)
            }

    return {
        "columns": columns,
        "rows": [{c: _normalise(c, row[c]) for c in columns} for row in rows],
        "row_limit_reached": limited,
        "missing_periods": missing_periods,
        "null_key_rows": null_key_rows,
    }
