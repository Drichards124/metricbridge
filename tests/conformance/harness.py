"""The conformance harness: cases, the normaliser and the comparison. Test support only."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import anyio
import duckdb
import sqlglot
import yaml
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from storefront_data import storefront_database

from metricbridge.contract import QueryRequest
from metricbridge.engine import DuckDBEngine
from metricbridge.manifest import ManifestError, load_manifest
from metricbridge.server import build_server

FLOAT_PLACES = 9
FIXTURES = Path(__file__).parent.parent / "fixtures"
# A case names its catalog: the manifest directory, and the function that seeds its database.
CATALOGS = {"storefront": (FIXTURES / "storefront", storefront_database)}


class CaseError(Exception):
    """A case file was refused: a typo in a case must fail loudly, not run a different question."""


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    catalog: str
    request: QueryRequest
    reference_sql: str | None = None
    refusal: str | None = None
    manifest_refusal_message: str | None = None

    @model_validator(mode="after")
    def _one_outcome(self) -> "Case":
        outcomes = [self.reference_sql, self.refusal, self.manifest_refusal_message]
        if sum(outcome is not None for outcome in outcomes) != 1:
            raise ValueError(
                "state exactly one of reference_sql, refusal or manifest_refusal_message"
            )
        if self.reference_sql is None:
            return self
        try:
            reference = sqlglot.parse_one(self.reference_sql, read="duckdb")
        except sqlglot.errors.ParseError as error:
            raise ValueError(f"reference_sql does not parse as DuckDB SQL: {error}") from None
        grouped = self.request.dimensions or self.request.time_grain
        # Rows are compared in order, so an unordered reference would pass or fail by chance.
        if grouped and not reference.args.get("order"):
            raise ValueError("a reference_sql returning grouped rows needs an outer ORDER BY")
        return self


def normalise(value: object) -> object:
    """Types only: the same value from two engines, or two scales, becomes one canonical form."""
    if isinstance(value, Decimal):
        if value == 0:
            return "0"  # an engine's -0 is the same zero
        return format(value.normalize(), "f")  # "f", or 100 would come back as 1E+2
    if isinstance(value, date):  # a datetime is a date too, and keeps its time
        return value.isoformat()
    if isinstance(value, float):
        # Engines can disagree in an average's last binary digits; nine places is an expectation,
        # unmeasured until the Postgres and ClickHouse runs.
        return format(value + 0.0, f".{FLOAT_PLACES}f")  # + 0.0 folds -0.0 into 0.0
    return value


def _read(value: object, like: object) -> object:
    """MetricBridge's JSON value, read as the type the reference returned for the same cell."""
    if value is None:
        return value
    if isinstance(like, Decimal):
        return Decimal(value)
    if isinstance(like, datetime):  # before date: a datetime is a date too
        return datetime.fromisoformat(value)  # "2026-07-01" reads as midnight
    if isinstance(like, date):
        return date.fromisoformat(value)
    return value


def compare(answer: dict, case: Case, reference: tuple[list[str], list[tuple]]) -> list[str]:
    """Every way MetricBridge's answer differs from the reference; empty when they agree."""
    expected = case.manifest_refusal_message
    if expected is not None:
        issues = answer.get("manifest_issues")
        if issues is None:
            return [f"manifest: expected a refusal containing {expected!r}, but it loaded"]
        if not any(expected in issue for issue in issues):
            return [f"manifest: expected a refusal containing {expected!r}, refused with {issues}"]
        return []
    codes = sorted({error["code"] for error in answer.get("errors", [])})
    if case.refusal is not None:
        if answer["ok"]:
            return [f"refusal: expected {case.refusal!r}, MetricBridge answered with rows"]
        if codes != [case.refusal]:
            return [f"refusal: expected {case.refusal!r}, MetricBridge refused with {codes}"]
        return []
    if not answer["ok"]:
        return [f"refusal: reference expected rows, MetricBridge refused with {codes}"]
    columns, rows = reference
    if answer["columns"] != columns:  # cells cannot be paired, so nothing further is compared
        return [f"columns: MetricBridge {answer['columns']}, reference {columns}"]
    differences = []
    if len(answer["rows"]) != len(rows):
        differences.append(
            f"row count: MetricBridge {len(answer['rows'])} rows, reference {len(rows)} rows"
        )
    for index, (got, expected) in enumerate(zip(answer["rows"], rows)):
        for column, like in zip(columns, expected):
            where = f"row {index}, {column}: MetricBridge {got[column]!r}, reference {like!r}"
            try:
                value = _read(got[column], like)
            except (ArithmeticError, TypeError, ValueError):  # InvalidOperation is arithmetic
                differences.append(f"{where}: not a {type(like).__name__}")
                continue
            if normalise(value) != normalise(like):
                differences.append(where)
    return differences


def load_case(path: Path) -> Case:
    try:
        case = Case.model_validate(yaml.safe_load(path.read_text()))
    except ValidationError as error:
        raise CaseError(f"{path.name}: {error}") from None
    if case.id != path.stem:  # one name per case, so a report line finds its file
        raise CaseError(f"{path.name}: id {case.id!r} must match the file name {path.stem!r}")
    return case


def _ask(server, request: dict) -> dict:
    """The answer an agent receives: the real query_metric tool, over in-memory streams."""

    async def run():
        low = server._lowlevel_server
        async with (
            create_client_server_memory_streams() as ((cr, cw), (sr, sw)),
            anyio.create_task_group() as group,
        ):
            group.start_soon(
                lambda: low.run(sr, sw, low.create_initialization_options(), raise_exceptions=True)
            )
            async with ClientSession(cr, cw) as session:
                await session.initialize()
                result = await session.call_tool("query_metric", request)
            group.cancel_scope.cancel()
            return result.structured_content

    return anyio.run(run)


def run_case(case: Case) -> tuple[dict, tuple[list[str], list[tuple]] | None]:
    """MetricBridge's answer to the case's request, and the reference's columns and rows."""
    manifest_directory, seed = CATALOGS[case.catalog]
    database = seed()
    try:
        server = build_server(load_manifest(manifest_directory), DuckDBEngine(database))
    except ManifestError as refused:  # no server exists, so no request is asked
        return {"ok": False, "manifest_issues": [str(issue) for issue in refused.issues]}, None
    answer = _ask(server, case.request.model_dump(mode="json", exclude_defaults=True))
    if case.reference_sql is None:
        return answer, None
    # The engine's own settings: DuckDB refuses a second connection to a file configured otherwise.
    with duckdb.connect(
        str(database),
        read_only=True,
        config={"enable_external_access": False, "lock_configuration": True},
    ) as connection:
        cursor = connection.execute(case.reference_sql)
        return answer, ([column[0] for column in cursor.description], cursor.fetchall())
